from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from app.api.deps import (
    CurrentUser,
    OptionalUser,
    OsrmDep,
    RedisDep,
    SessionDep,
    SettingsDep,
    client_ip,
)
from app.api.limits import rate_limit_user
from app.core.errors import ForbiddenError, NotFoundError
from app.db.models import Role
from app.schemas.common import Page
from app.schemas.routes import (
    BuildRouteRequest,
    BuildRouteResponse,
    LikeState,
    RouteCreate,
    RouteDetail,
    RouteOut,
    RouteSort,
    RouteUpdate,
)
from app.services import routes as svc
from app.services import views as views_service

router = APIRouter(prefix="/routes", tags=["routes"])
expensive_limit = Depends(rate_limit_user("build", "rate_limit_expensive_per_minute"))


def _not_found() -> NotFoundError:
    return NotFoundError("Route not found", code="route_not_found")


@router.post("/build", response_model=BuildRouteResponse, dependencies=[expensive_limit])
async def build_route(
    payload: BuildRouteRequest,
    user: CurrentUser,
    redis: RedisDep,
    osrm: OsrmDep,
    settings: SettingsDep,
):
    built = await svc.build_route(
        redis=redis,
        osrm=osrm,
        settings=settings,
        points=[(w.lat, w.lng) for w in payload.waypoints],
    )
    return {
        "geometry": {"type": "LineString", "coordinates": [list(c) for c in built.coordinates]},
        "duration": built.duration_s,
        "distance": built.distance_m,
    }


@router.post(
    "",
    response_model=RouteDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[expensive_limit],
)
async def create_route(
    payload: RouteCreate,
    user: CurrentUser,
    session: SessionDep,
    redis: RedisDep,
    osrm: OsrmDep,
    settings: SettingsDep,
):
    tag_ids = await svc.resolve_tag_ids(session, payload.tag_ids)
    built = await svc.build_route(
        redis=redis,
        osrm=osrm,
        settings=settings,
        points=[(w.lat, w.lng) for w in payload.waypoints],
    )
    route_id = await svc.create_route(
        session, redis, author=user, data=payload, built=built, tag_ids=tag_ids
    )
    return await svc.get_route_detail(session, route_id, user)


@router.get("", response_model=Page[RouteOut])
async def list_routes(
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    viewer: OptionalUser,
    lat: Annotated[float | None, Query(ge=-90, le=90, allow_inf_nan=False)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180, allow_inf_nan=False)] = None,
    radius: Annotated[int | None, Query(ge=1, le=100_000, description="meters")] = None,
    duration_min: Annotated[int | None, Query(ge=0, description="seconds")] = None,
    duration_max: Annotated[int | None, Query(ge=0, description="seconds")] = None,
    tags: Annotated[list[str] | None, Query(max_length=10, description="tag names")] = None,
    tags_match: Literal["any", "all"] = "any",
    sort: RouteSort = RouteSort.POPULAR,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    filters = svc.RouteFilters(
        lat=lat,
        lng=lng,
        radius=radius,
        duration_min=duration_min,
        duration_max=duration_max,
        tags=tags,
        tags_match=tags_match,
        sort=sort,
        page=page,
        limit=limit,
    )
    return await svc.list_public_routes(session, redis, settings, filters, viewer)


@router.get("/{route_id}", response_model=RouteDetail)
async def get_route(
    route_id: int,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    viewer: OptionalUser,
):
    route = await svc.get_route_detail(session, route_id, viewer)
    if route is None:
        raise _not_found()
    if route["is_public"] and not route["is_hidden"]:
        background.add_task(
            views_service.record_view,
            redis,
            settings,
            route_id=route["id"],
            author_id=route["author"]["id"],
            user_id=viewer.id if viewer else None,
            client_ip=client_ip(request),
        )
    return route


def _ensure_can_modify(route, user) -> None:
    if route is None or not svc.can_view(route, user):
        raise _not_found()
    if route.author_id != user.id and user.role != Role.ADMIN.value:
        raise ForbiddenError("You can only modify your own routes", code="not_route_owner")


@router.put("/{route_id}", response_model=RouteDetail, dependencies=[expensive_limit])
async def update_route(
    route_id: int,
    payload: RouteUpdate,
    user: CurrentUser,
    session: SessionDep,
    redis: RedisDep,
    osrm: OsrmDep,
    settings: SettingsDep,
):
    route = await svc.get_route_orm(session, route_id)
    _ensure_can_modify(route, user)
    tag_ids = (
        await svc.resolve_tag_ids(session, payload.tag_ids) if payload.tag_ids is not None else None
    )
    built = None
    if payload.waypoints is not None:
        built = await svc.build_route(
            redis=redis,
            osrm=osrm,
            settings=settings,
            points=[(w.lat, w.lng) for w in payload.waypoints],
        )
    await svc.update_route(session, redis, route, payload, built=built, tag_ids=tag_ids)
    return await svc.get_route_detail(session, route_id, user)


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(route_id: int, user: CurrentUser, session: SessionDep, redis: RedisDep):
    route = await svc.get_route_orm(session, route_id)
    _ensure_can_modify(route, user)
    await svc.delete_route(session, redis, route)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{route_id}/like", response_model=LikeState)
async def like(route_id: int, user: CurrentUser, session: SessionDep, redis: RedisDep):
    return await svc.like_route(session, redis, user, route_id)


@router.delete("/{route_id}/like", response_model=LikeState)
async def unlike(route_id: int, user: CurrentUser, session: SessionDep, redis: RedisDep):
    return await svc.unlike_route(session, redis, user, route_id)
