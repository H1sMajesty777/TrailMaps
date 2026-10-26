import os
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# --- environment must be prepared BEFORE the application is imported -------------------------
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://trailmaps:trailmaps@localhost:5432/trailmaps_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "CONFIGURE_LOGGING": "false",
        "JWT_SECRET": "test-secret-test-secret-test-secret-0123456789",
        "BCRYPT_ROUNDS": "4",
        "DB_USE_NULL_POOL": "true",
        "VIEWS_FLUSH_INTERVAL_SECONDS": "0",
        "LIST_CACHE_TTL_SECONDS": "0",
        "UPLOAD_DIR": tempfile.mkdtemp(prefix="trailmaps-uploads-"),
        "RATE_LIMIT_GLOBAL_PER_MINUTE": "10000",
        "RATE_LIMIT_AUTH_PER_MINUTE": "10000",
        "RATE_LIMIT_EXPENSIVE_PER_MINUTE": "10000",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.helpers import Actor, FakeOSRM, make_actor  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


def assert_test_database(database_url: str) -> None:
    db_name = urlparse(database_url).path.lstrip("/")
    if "test" not in db_name:
        raise pytest.UsageError(
            f"Refusing to run: database '{db_name}' does not look like a test database "
            "(tests TRUNCATE all tables)."
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    assert_test_database(get_settings().database_url)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Apply Alembic migrations once per session (this also tests the migrations)."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")


async def reset_state(settings: Settings) -> None:
    assert_test_database(settings.database_url)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE route_views, likes, complaints, route_tags, routes, tags, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()
    redis = Redis.from_url(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return get_settings().model_copy(update={"upload_dir": str(tmp_path / "uploads")})


@pytest.fixture
def fake_osrm() -> FakeOSRM:
    return FakeOSRM()


@pytest_asyncio.fixture
async def app_factory(
    settings: Settings, fake_osrm: FakeOSRM, migrated_database: None
) -> AsyncIterator[Callable[..., Any]]:
    """Build an app with a live lifespan (DB engine, Redis, ...) and a fake OSRM backend."""
    stack = AsyncExitStack()

    async def factory(**overrides: Any):
        app_settings = settings.model_copy(update=overrides)
        await reset_state(app_settings)
        application = create_app(app_settings)
        await stack.enter_async_context(application.router.lifespan_context(application))
        application.state.osrm = fake_osrm.make_client(
            app_settings.osrm_url, app_settings.osrm_profile
        )
        return application

    try:
        yield factory
    finally:
        await stack.aclose()
        await fake_osrm.aclose()


@pytest_asyncio.fixture
async def app(app_factory):
    return await app_factory()


def make_client(application) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as c:
        yield c


@pytest_asyncio.fixture
async def alice(app, client) -> Actor:
    return await make_actor(app, client, "alice")


@pytest_asyncio.fixture
async def bob(app, client) -> Actor:
    return await make_actor(app, client, "bob")


@pytest_asyncio.fixture
async def manager(app, client) -> Actor:
    return await make_actor(app, client, "manny", role="manager")


@pytest_asyncio.fixture
async def admin(app, client) -> Actor:
    return await make_actor(app, client, "root", role="admin")
