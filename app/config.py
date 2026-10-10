"""Central configuration. Every value can be overridden with an UPIMESH_* env var."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPIMESH_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./upimesh.db"
    # Optional: load the server RSA key from PEM instead of generating one at boot.
    private_key_path: str | None = None
    # Optional: use Redis (SET NX EX) for idempotency instead of the in-process store.
    redis_url: str | None = None

    packet_ttl: int = 5
    max_packet_age_seconds: int = 24 * 3600
    max_clock_skew_seconds: int = 120
    idempotency_ttl_seconds: int = 24 * 3600
    max_txn_paise: int = 10_000_000  # Rs 1,00,000 per offline transaction (demo value)
    bridge_rate_limit_per_minute: int = 600
    eviction_interval_seconds: int = 60
    seed_demo_data: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
