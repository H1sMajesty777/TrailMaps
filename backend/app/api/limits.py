"""Per-endpoint rate limit dependencies (on top of the global middleware limiter)."""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from redis.exceptions import RedisError

from app.api.deps import CurrentUser, client_ip
from app.core.errors import TooManyRequestsError
from app.core.ratelimit import consume

logger = logging.getLogger(__name__)


async def _enforce(request: Request, key: str, limit: int) -> None:
    try:
        allowed, retry_after = await consume(request.app.state.redis, key, limit, 60)
    except RedisError:
        logger.warning("Rate limiter unavailable, failing open")
        return
    if not allowed:
        raise TooManyRequestsError("Rate limit exceeded", headers={"Retry-After": str(retry_after)})


def rate_limit_ip(scope: str, limit_attr: str) -> Callable[..., Coroutine[Any, Any, None]]:
    async def dependency(request: Request) -> None:
        settings = request.app.state.settings
        if settings.rate_limit_enabled:
            key = f"rl:{scope}:ip:{client_ip(request)}"
            await _enforce(request, key, getattr(settings, limit_attr))

    return dependency


def rate_limit_user(scope: str, limit_attr: str) -> Callable[..., Coroutine[Any, Any, None]]:
    async def dependency(request: Request, user: CurrentUser) -> None:
        settings = request.app.state.settings
        if settings.rate_limit_enabled:
            key = f"rl:{scope}:user:{user.id}"
            await _enforce(request, key, getattr(settings, limit_attr))

    return dependency
