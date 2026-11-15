from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://fin_oms:fin_oms@localhost:5432/fin_oms"
    sql_echo: bool = False
    # DB connection pool, per process. Production sets these so that
    # max instances x pool size fits the database's connection limit
    # (terraform/cloudrun.tf); the defaults are SQLAlchemy's own.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    # Per-client token bucket (app/rate_limit.py), per process.
    rate_limit_per_second: float = 5.0
    rate_limit_burst: int = 20

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
