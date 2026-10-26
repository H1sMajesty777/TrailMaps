from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.api.limits import rate_limit_user
from app.core.errors import NotFoundError
from app.db.models import Role, User
from app.schemas.common import Page
from app.schemas.routes import RouteOut
from app.schemas.users import UserMe, UserPublic, UserUpdate
from app.services import avatars, users as users_service
from app.services import routes as routes_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserMe)
async def get_me(user: CurrentUser):
    return user


@router.put("/me", response_model=UserMe)
async def update_me(payload: UserUpdate, user: CurrentUser, session: SessionDep):
    return await users_service.update_profile(session, user, payload)


@router.post(
    "/me/avatar",
    response_model=UserMe,
    dependencies=[Depends(rate_limit_user("avatar", "rate_limit_expensive_per_minute"))],
)
async def upload_avatar(
    file: Annotated[UploadFile, File(description="JPEG, PNG or WEBP image")],
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
):
    new_url = await avatars.store_avatar(file, user.id, settings)
    old_url = user.avatar_url
    user.avatar_url = new_url
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        avatars.delete_avatar_file(new_url, settings)
        raise
    avatars.delete_avatar_file(old_url, settings)
    return user


@router.get("/me/routes", response_model=Page[RouteOut])
async def my_routes(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await routes_service.list_user_routes(session, settings, user, page, limit)


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(user_id: int, viewer: CurrentUser, session: SessionDep):
    target = await session.get(User, user_id)
    is_admin = viewer.role == Role.ADMIN.value
    if target is None or (not target.is_active and not is_admin):
        raise NotFoundError("User not found", code="user_not_found")
    return target
