"""FastAPI dependencies: settings, DB session, Redis, OSRM, authentication, RBAC."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.models import Role, User
from app.services import auth as auth_service
from app.services.osrm import OSRMClient

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_osrm(request: Request) -> OSRMClient:
    return request.app.state.osrm


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
OsrmDep = Annotated[OSRMClient, Depends(get_osrm)]


async def get_optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> User | None:
    """Anonymous access is allowed, but a *presented* token must be valid."""
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials, settings, "access")
    if await auth_service.is_revoked(redis, payload.jti):
        raise UnauthorizedError("Token has been revoked", code="token_revoked")
    user = await session.get(User, payload.user_id)
    if user is None:
        raise UnauthorizedError("Invalid token", code="invalid_token")
    if not user.is_active:
        raise ForbiddenError("Account is disabled", code="account_disabled")
    request.state.token_payload = payload
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_current_user(user: OptionalUser) -> User:
    if user is None:
        raise UnauthorizedError("Not authenticated", code="not_authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., Coroutine[Any, Any, User]]:
    allowed = {role.value for role in roles}

    async def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise ForbiddenError("Insufficient permissions", code="insufficient_role")
        return user

    return dependency


StaffUser = Annotated[User, Depends(require_roles(Role.MANAGER, Role.ADMIN))]
AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
