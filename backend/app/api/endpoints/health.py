import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}

    async def check_db() -> None:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await request.app.state.redis.ping()

    for name, check in (("database", check_db), ("redis", check_redis)):
        try:
            await asyncio.wait_for(check(), timeout=2)
            checks[name] = "ok"
        except Exception as exc:
            logger.warning("Readiness check failed: %s (%s)", name, exc.__class__.__name__)
            checks[name] = "unavailable"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
