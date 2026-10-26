"""Registration, login, token refresh/rotation and logout (Redis JWT blacklist)."""

import logging
import time
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.security import (
    TokenPayload,
    burn_password_check,
    create_token,
    decode_token,
    hash_password_async,
    verify_password_async,
)
from app.db.models import Role, User
from app.schemas.auth import RegisterRequest, TokenPair

logger = logging.getLogger(__name__)

BLACKLIST_PREFIX = "bl:"
LOGIN_FAIL_PREFIX = "login_fail:"


def issue_token_pair(user_id: int, settings: Settings) -> TokenPair:
    access, _ = create_token(user_id, "access", settings)
    refresh, _ = create_token(user_id, "refresh", settings)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        refresh_expires_in=settings.refresh_token_ttl_days * 86400,
    )


def _backend_unavailable(exc: Exception) -> ServiceUnavailableError:
    logger.error("Redis unavailable for auth operation: %s", exc)
    return ServiceUnavailableError(
        "Authentication backend is unavailable", code="auth_backend_unavailable"
    )


async def is_revoked(redis: Redis, jti: str) -> bool:
    """Fail closed: if the blacklist cannot be consulted, the token cannot be trusted."""
    try:
        return bool(await redis.exists(BLACKLIST_PREFIX + jti))
    except RedisError as exc:
        raise _backend_unavailable(exc) from exc


async def revoke(redis: Redis, payload: TokenPayload, *, only_if_new: bool = False) -> bool:
    ttl = max(payload.expires_at - int(time.time()), 1)
    try:
        result = await redis.set(BLACKLIST_PREFIX + payload.jti, "1", ex=ttl, nx=only_if_new)
    except RedisError as exc:
        raise _backend_unavailable(exc) from exc
    return bool(result)


async def register_user(session: AsyncSession, settings: Settings, data: RegisterRequest) -> User:
    if await session.scalar(select(User.id).where(User.email == data.email)) is not None:
        raise ConflictError("Email is already registered", code="email_taken")
    username_taken = await session.scalar(
        select(User.id).where(func.lower(User.username) == data.username.lower())
    )
    if username_taken is not None:
        raise ConflictError("Username is already taken", code="username_taken")

    user = User(
        email=data.email,
        username=data.username,
        phone=data.phone,
        password_hash=await hash_password_async(data.password, settings.bcrypt_rounds),
        role=Role.USER.value,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("User already exists", code="user_exists") from exc
    return user


async def _failures(redis: Redis, key: str) -> tuple[int, int]:
    try:
        count = int(await redis.get(key) or 0)
        ttl = await redis.ttl(key) if count else 0
    except RedisError:
        return 0, 0  # throttling fails open
    return count, max(int(ttl), 1)


async def _register_failure(redis: Redis, key: str, window: int) -> None:
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            await pipe.execute()
    except RedisError:
        logger.warning("Could not record failed login attempt")


async def login(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    email: str,
    password: str,
    client_ip: str,
) -> TokenPair:
    fail_key = f"{LOGIN_FAIL_PREFIX}{client_ip}:{email}"
    count, retry_after = await _failures(redis, fail_key)
    if count >= settings.login_max_failures:
        raise TooManyRequestsError(
            "Too many failed login attempts, try again later",
            code="too_many_attempts",
            headers={"Retry-After": str(retry_after)},
        )

    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        await burn_password_check(password, settings.bcrypt_rounds)
        valid = False
    else:
        valid = await verify_password_async(password, user.password_hash)

    if user is None or not valid:
        await _register_failure(redis, fail_key, settings.login_lockout_seconds)
        raise UnauthorizedError("Invalid email or password", code="invalid_credentials")
    if not user.is_active:
        raise ForbiddenError("Account is disabled", code="account_disabled")

    try:
        await redis.delete(fail_key)
    except RedisError:
        pass
    user.last_login = datetime.now(UTC)
    await session.commit()
    return issue_token_pair(user.id, settings)


async def refresh_tokens(
    session: AsyncSession, redis: Redis, settings: Settings, refresh_token: str
) -> TokenPair:
    payload = decode_token(refresh_token, settings, "refresh")
    user = await session.get(User, payload.user_id)
    if user is None:
        raise UnauthorizedError("Invalid token", code="invalid_token")
    if not user.is_active:
        raise ForbiddenError("Account is disabled", code="account_disabled")
    # rotation: a refresh token can be used exactly once (atomic SET NX)
    if not await revoke(redis, payload, only_if_new=True):
        raise UnauthorizedError("Refresh token has been revoked", code="token_revoked")
    return issue_token_pair(user.id, settings)


async def logout(
    redis: Redis, settings: Settings, access: TokenPayload, refresh_token: str | None
) -> None:
    await revoke(redis, access)
    if not refresh_token:
        return
    try:
        refresh = decode_token(refresh_token, settings, "refresh")
    except UnauthorizedError:
        return  # already invalid/expired: nothing to revoke
    if refresh.user_id == access.user_id:
        await revoke(redis, refresh)
