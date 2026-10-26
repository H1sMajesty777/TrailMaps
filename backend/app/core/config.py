"""Application settings, loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_PREFIX = "dev-insecure"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TrailMaps API"
    app_version: str = "1.0.0"
    environment: Literal["dev", "test", "staging", "production"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    configure_logging: bool = True

    # --- PostgreSQL / PostGIS ---
    database_url: str = "postgresql+asyncpg://trailmaps:trailmaps@localhost:5432/trailmaps"
    db_pool_size: int = Field(10, ge=1)
    db_max_overflow: int = Field(20, ge=0)
    db_statement_timeout_ms: int = Field(30_000, ge=0)
    db_use_null_pool: bool = False
    db_echo: bool = False

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- OSRM ---
    osrm_url: str = "http://osrm:5000"
    osrm_profile: str = "foot"
    osrm_timeout_seconds: float = Field(10.0, gt=0)
    route_cache_ttl_seconds: int = Field(3600, ge=0)
    max_route_distance_m: int = Field(200_000, ge=100)

    # --- Auth ---
    jwt_secret: str = INSECURE_SECRET_PREFIX + "-secret-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "trailmaps"
    access_token_ttl_minutes: int = Field(15, ge=1)
    refresh_token_ttl_days: int = Field(30, ge=1)
    bcrypt_rounds: int = Field(12, ge=4, le=16)
    login_max_failures: int = Field(5, ge=1)
    login_lockout_seconds: int = Field(900, ge=1)

    # --- Rate limiting (per minute) ---
    rate_limit_enabled: bool = True
    rate_limit_global_per_minute: int = Field(300, ge=1)
    rate_limit_auth_per_minute: int = Field(10, ge=1)
    rate_limit_expensive_per_minute: int = Field(30, ge=1)

    # --- Views counter / caching ---
    views_flush_interval_seconds: int = Field(30, ge=0)  # 0 disables the background flusher
    view_dedup_window_seconds: int = Field(1800, ge=1)
    list_cache_ttl_seconds: int = Field(60, ge=0)  # 0 disables the list cache
    list_geometry_tolerance_deg: float = Field(0.0001, ge=0)

    # --- Files ---
    upload_dir: str = "./uploads"
    uploads_url_prefix: str = "/uploads"
    avatar_max_bytes: int = Field(5 * 1024 * 1024, ge=1)
    avatar_size_px: int = Field(512, ge=32, le=2048)

    # --- HTTP ---
    cors_origins: str = ""  # comma separated list of allowed origins

    # --- First admin bootstrap (optional) ---
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_username: str = "admin"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Swagger/OpenAPI is exposed in the dev environment only (spec, section 10)."""
        return self.environment == "dev"

    @property
    def is_production_like(self) -> bool:
        return self.environment in ("staging", "production")

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if self.is_production_like:
            if self.jwt_secret.startswith(INSECURE_SECRET_PREFIX) or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be a random string of at least 32 characters")
            if "*" in self.cors_origin_list:
                raise ValueError("CORS_ORIGINS must list explicit domains, not '*'")
            if self.bootstrap_admin_password and len(self.bootstrap_admin_password) < 12:
                raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
