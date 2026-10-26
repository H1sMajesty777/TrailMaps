"""Redis fixed-window rate limiting: primitive + global ASGI middleware."""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import error_payload

logger = logging.getLogger(__name__)


async def consume(redis: Redis, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Count one hit. Returns (allowed, retry_after_seconds). Needs Redis >= 7 (EXPIRE NX)."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    return int(count) <= limit, max(int(ttl), 1)


class RateLimitMiddleware:
    """Global per-IP limiter. Fails open if Redis is unavailable (availability over strictness)."""

    EXEMPT_PREFIXES = ("/api/health",)

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        state = scope["app"].state
        redis: Redis | None = getattr(state, "redis", None)
        settings = state.settings
        if (
            redis is None
            or not settings.rate_limit_enabled
            or scope["path"].startswith(self.EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "unknown"
        try:
            allowed, retry_after = await consume(
                redis, f"rl:global:{ip}", settings.rate_limit_global_per_minute, 60
            )
        except RedisError:
            logger.warning("Rate limiter unavailable, failing open")
            allowed, retry_after = True, 0

        if not allowed:
            request_id = scope.get("state", {}).get("request_id")
            response = JSONResponse(
                status_code=429,
                content=error_payload(request_id, "too_many_requests", "Rate limit exceeded"),
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
