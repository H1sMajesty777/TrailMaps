from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, RedisDep, SessionDep, SettingsDep, client_ip
from app.api.limits import rate_limit_ip
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.users import UserMe
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
auth_limit = Depends(rate_limit_ip("auth", "rate_limit_auth_per_minute"))


@router.post(
    "/register",
    response_model=UserMe,
    status_code=status.HTTP_201_CREATED,
    dependencies=[auth_limit],
)
async def register(payload: RegisterRequest, session: SessionDep, settings: SettingsDep):
    return await auth_service.register_user(session, settings, payload)


@router.post("/login", response_model=TokenPair, dependencies=[auth_limit])
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
):
    return await auth_service.login(
        session,
        redis,
        settings,
        email=payload.email,
        password=payload.password,
        client_ip=client_ip(request),
    )


@router.post("/refresh", response_model=TokenPair, dependencies=[auth_limit])
async def refresh(
    payload: RefreshRequest, session: SessionDep, redis: RedisDep, settings: SettingsDep
):
    return await auth_service.refresh_tokens(session, redis, settings, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    user: CurrentUser,
    redis: RedisDep,
    settings: SettingsDep,
    payload: LogoutRequest | None = None,
) -> Response:
    refresh_token = payload.refresh_token if payload else None
    await auth_service.logout(redis, settings, request.state.token_payload, refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser):
    return user
