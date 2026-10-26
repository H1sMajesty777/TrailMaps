"""Application factory and lifespan (startup/shutdown of DB, Redis, OSRM client, workers)."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import create_engine, create_session_factory
from app.services.bootstrap import ensure_admin
from app.services.osrm import OSRMClient
from app.services.views import flush_views

logger = logging.getLogger(__name__)


async def _views_flush_loop(app: FastAPI, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            flushed = await flush_views(app.state.redis, app.state.session_factory)
            if flushed:
                logger.info("Flushed %d buffered route views", flushed)
        except Exception:
            logger.exception("Views flush failed, will retry")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.configure_logging:
        configure_logging(settings.log_level, settings.log_json)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    http_client = httpx.AsyncClient(
        timeout=settings.osrm_timeout_seconds,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.osrm = OSRMClient(settings.osrm_url, http_client, settings.osrm_profile)

    (Path(settings.upload_dir) / "avatars").mkdir(parents=True, exist_ok=True)
    os.umask(0o022)

    try:
        await ensure_admin(session_factory, settings)
    except Exception:
        logger.exception("Bootstrap admin failed (is the database migrated?)")

    flusher = None
    if settings.views_flush_interval_seconds > 0:
        flusher = asyncio.create_task(
            _views_flush_loop(app, settings.views_flush_interval_seconds)
        )
    logger.info("Application started (environment=%s)", settings.environment)
    try:
        yield
    finally:
        if flusher is not None:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
        try:
            await flush_views(redis, session_factory)
        except Exception:
            logger.exception("Final views flush failed")
        await http_client.aclose()
        await redis.aclose()
        await engine.dispose()
        logger.info("Application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings

    # last added = outermost: request context -> CORS -> rate limit -> app
    app.add_middleware(RateLimitMiddleware)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
