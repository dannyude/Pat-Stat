"""
Configuration management module for the PatStat backend.

This module uses Pydantic Settings to load configuration variables from the
environment and a local `.env` file. It provides strong typing and basic
validation for environment variables.
"""

from functools import lru_cache
from typing import List, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    [Architecture/Config]: Application settings and configuration properties.
    By inheriting from BaseSettings, Pydantic automatically reads from the runtime OS
    environment, falling back to reading `.env` locally.

    Attributes are case-sensitive. Type coercion properties are provided via `@property`
    decorators so components downstream don't have to cast strings manually.
    """

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    # ─── App Settings ─────────────────────────────────────────────────────────────
    APP_ENV: Literal["development", "testing", "staging", "production"]
    SECRET_KEY: str = Field(min_length=32)
    APP_HOST: str
    APP_PORT: int = Field(ge=1, le=65535)
    DEBUG: bool = False

    # ─── Database ─────────────────────────────────────────────────────────────────
    DATABASE_URL: str  # [DB]: Async SQLAlchemy URL utilizing asyncpg
    DATABASE_URL_SYNC: str  # [Task Queue]: Sync URL used strictly by Celery workers

    # ─── Redis ────────────────────────────────────────────────────────────────────
    REDIS_URL: str  # [PubSub]: Main Redis URL used by WebSockets for Real-Time feed
    REDIS_CELERY_URL: str  # [Task Queue]: Redis URL used by Celery for broker/results
    REDIS_SESSION_DB: int = Field(ge=0)

    # ─── JWT ──────────────────────────────────────────────────────────────────────
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(ge=5, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(ge=1, le=90)

    # ─── Firebase ─────────────────────────────────────────────────────────────────
    FIREBASE_CREDENTIALS_PATH: str  # Path to the Firebase service account JSON file
    FCM_PROJECT_ID: str

    # ─── Celery ───────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str

    # ─── CORS ─────────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str  # [Security]: Which frontends can hit this API

    # ─── Rate Limiting ────────────────────────────────────────────────────────────
    API_RATE_LIMIT_DEFAULT: str = "120/minute"
    AUTH_RATE_LIMIT: str = "100/minute"
    WRITE_RATE_LIMIT: str = "30/minute"

    # ─── Type-Coerced Properties ──────────────────────────────────────────────────

    @model_validator(mode="after")
    def validate_runtime_safety(self):
        insecure_placeholders = {
            "your-super-secret-key-change-in-production-min-32-chars",
            "changeme",
            "change-me",
            "replace-me",
        }
        if self.APP_ENV == "production":
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production")
            secret_key = str(self.SECRET_KEY).strip().lower()
            if secret_key in insecure_placeholders:
                raise ValueError("SECRET_KEY must not use placeholder values")
            if "*" in self.allowed_origins_list:
                raise ValueError("ALLOWED_ORIGINS cannot include '*' in production")
        return self

    @property
    def debug(self) -> bool:
        return self.DEBUG

    @property
    def app_port(self) -> int:
        return self.APP_PORT

    @property
    def redis_session_db(self) -> int:
        return self.REDIS_SESSION_DB

    @property
    def access_token_expire_minutes(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES

    @property
    def refresh_token_expire_days(self) -> int:
        return self.REFRESH_TOKEN_EXPIRE_DAYS

    @property
    def api_rate_limit_default(self) -> str:
        return self.API_RATE_LIMIT_DEFAULT

    @property
    def auth_rate_limit(self) -> str:
        return self.AUTH_RATE_LIMIT

    @property
    def write_rate_limit(self) -> str:
        return self.WRITE_RATE_LIMIT

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """
    [Performance]: Retrieves the application settings, caching the result to avoid redundant
    file I/O of reading `.env` multiple times on every import.
    """
    return Settings()


# [Architecture]: Singleton pattern instance exported for immediate use throughout the codebase.
settings = get_settings()

# ─── Platform Business-Rule Constants ─────────────────────────────────────────
# Hard cap on super-admin accounts. Enforced by both the bootstrap CLI script
# (scripts/seed_super_admin.py) and the API service layer (backoffice/services.py).
MAX_SUPER_ADMINS: int = 3

__all__ = ["Settings", "get_settings", "settings", "MAX_SUPER_ADMINS"]
