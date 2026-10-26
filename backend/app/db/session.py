from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
        "connect_args": {
            "server_settings": {
                "timezone": "UTC",
                "application_name": "trailmaps-api",
                "statement_timeout": str(settings.db_statement_timeout_ms),
            }
        },
    }
    if settings.db_use_null_pool:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
    return create_async_engine(settings.database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
