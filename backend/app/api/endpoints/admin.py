from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, RedisDep, SessionDep, StaffUser
from app.core.errors import NotFoundError
from app.db.models import Role, Route
from app.schemas.admin import (
    AdminUserOut,
    AdminUserUpdate,
    Granularity,
    StatsOverview,
    UsersStats,
    ViewsStats,
)
from app.schemas.common import Page
from app.schemas.routes import VisibilityOut, VisibilityUpdate
from app.services import admin as admin_service
from app.services import routes as routes_service
from app.services import stats as stats_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=Page[AdminUserOut])
async def list_users(
    _: AdminUser,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Role | None = None,
    is_active: bool | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await admin_service.list_users(
        session, q=q, role=role, is_active=is_active, page=page, limit=limit
    )


@router.put("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int, payload: AdminUserUpdate, actor: AdminUser, session: SessionDep
):
    return await admin_service.update_user(session, actor, user_id, payload)


@router.put("/routes/{route_id}/visibility", response_model=VisibilityOut)
async def set_route_visibility(
    route_id: int,
    payload: VisibilityUpdate,
    _: AdminUser,
    session: SessionDep,
    redis: RedisDep,
):
    route = await session.get(Route, route_id)
    if route is None:
        raise NotFoundError("Route not found", code="route_not_found")
    await routes_service.set_hidden(session, redis, route, payload.is_hidden)
    return {"id": route.id, "is_hidden": route.is_hidden}


@router.get("/stats", response_model=StatsOverview)
async def stats_overview(_: AdminUser, session: SessionDep):
    return await stats_service.overview(session)


@router.get("/stats/users", response_model=UsersStats)
async def stats_users(
    _: StaffUser,
    session: SessionDep,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: Granularity = Granularity.DAY,
):
    start, end = stats_service.resolve_period(date_from, date_to, granularity)
    return await stats_service.users_stats(session, start, end, granularity)


@router.get("/stats/views", response_model=ViewsStats)
async def stats_views(
    _: StaffUser,
    session: SessionDep,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: Granularity = Granularity.DAY,
):
    start, end = stats_service.resolve_period(date_from, date_to, granularity)
    return await stats_service.views_stats(session, start, end, granularity)
