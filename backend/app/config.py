"""All runtime settings, read from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://seats:seats@localhost:5432/seatsentinel"
    redis_url: str = "redis://localhost:6372/0"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 120
    # 12 is a sensible production cost. Tests lower it to keep signups fast.
    bcrypt_rounds: int = 12

    # Seat holds live in Redis and expire on their own (spec: 5 minutes).
    hold_ttl_seconds: int = 300

    # Optimistic locking: how many times to re-read and retry after a version
    # conflict before giving up with a clean 409.
    optimistic_max_retries: int = 5
    # Redis lock: auto-expiry so a crashed worker cannot block a seat forever.
    # Must be longer than the slowest booking transaction.
    redis_lock_ttl_ms: int = 5000

    db_pool_size: int = 20
    db_max_overflow: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
