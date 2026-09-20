from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Costura Óptima API"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://costura:costura@localhost:5432/costura_optima"
    cors_origins: str = "http://localhost:5173"
    redis_url: str = "redis://localhost:6379/0"
    optimization_queue_name: str = "optimization"
    optimization_inline: bool = False
    default_optimization_timeout: int = 120
    max_request_bytes: int = 1_000_000
    worker_stale_seconds: int = 90
    planner_refinement_engine: str = "hybrid"
    joint_optimization_enabled: bool = False
    persistent_catalog_bootstrap_enabled: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
