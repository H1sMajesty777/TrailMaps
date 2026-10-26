"""Route building (OSRM + Redis cache), CRUD, geo search, likes, list cache."""

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import WKTElement
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import (
    ColumnElement,
    and_,
    delete,
    desc,
    func,
    literal_column,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.core.geo import point_ewkt, simplify_coords, to_linestring_wkt
from app.db.models import Like, Role, Route, RouteTag, Tag, User
from app.schemas.common import make_page
from app.schemas.routes import RouteCreate, RouteSort, RouteUpdate
from app.services.osrm import OSRMClient

logger = logging.getLogger(__name__)

OSRM_CACHE_PREFIX = "osrm:route:"
LIST_VERSION_KEY = "routes:list:version"
LIST_CACHE_PREFIX = "routes:list:"
MIN_ROUTE_DISTANCE_M = 10
DEFAULT_RADIUS_M = 5000


# --------------------------------------------------------------------------- building


@dataclass(frozen=True, slots=True)
class BuiltRoute:
    coordinates: list[tuple[float, float]]  # (lng, lat)
    distance_m: int
    duration_s: int


async def build_route(
    *,
    redis: Redis,
    osrm: OSRMClient,
    settings: Settings,
    points: Sequence[tuple[float, float]],  # (lat, lng)
) -> BuiltRoute:
    digest = hashlib.sha256(
        ";".join(f"{lat:.6f},{lng:.6f}" for lat, lng in points).encode()
    ).hexdigest()
    key = OSRM_CACHE_PREFIX + digest

    if settings.route_cache_ttl_seconds > 0:
        try:
            cached = await redis.get(key)
        except RedisError:
            cached = None
        if cached:
            try:
                data = json.loads(cached)
                return BuiltRoute(
                    coordinates=[(float(c[0]), float(c[1])) for c in data["c"]],
                    distance_m=int(data["d"]),
                    duration_s=int(data["t"]),
                )
            except (ValueError, KeyError, TypeError):
                logger.warning("Corrupted OSRM cache entry %s", key)

    result = await osrm.route(points)
    if result.distance_m > settings.max_route_distance_m:
        raise UnprocessableError(
            f"Route is longer than {settings.max_route_distance_m} m", code="route_too_long"
        )
    if result.distance_m < MIN_ROUTE_DISTANCE_M:
        raise UnprocessableError("Route is too short", code="route_too_short")

    built = BuiltRoute(result.coordinates, result.distance_m, result.duration_s)
    if settings.route_cache_ttl_seconds > 0:
        payload = json.dumps(
            {"c": built.coordinates, "d": built.distance_m, "t": built.duration_s},
            separators=(",", ":"),
        )
        try:
            await redis.set(key, payload, ex=settings.route_cache_ttl_seconds)
        except RedisError:
            logger.warning("Could not cache OSRM route")
    return built


# --------------------------------------------------------------------------- visibility


def read_visibility(viewer: User | None) -> ColumnElement[bool]:
    """Who can read a route: everyone sees public & not hidden; authors their own; staff all."""
    public = and_(Route.is_public.is_(True), Route.is_hidden.is_(False))
    if viewer is None:
        return public
    if viewer.role in (Role.MANAGER.value, Role.ADMIN.value):
        return true()
    return or_(public, Route.author_id == viewer.id)


def likeable_visibility(viewer: User) -> ColumnElement[bool]:
    public = and_(Route.is_public.is_(True), Route.is_hidden.is_(False))
    return or_(public, Route.author_id == viewer.id)


# --------------------------------------------------------------------------- cache version


async def bump_list_version(redis: Redis) -> None:
    try:
        await redis.incr(LIST_VERSION_KEY)
    except RedisError:
        logger.warning("Could not invalidate routes list cache")


# --------------------------------------------------------------------------- querying


def _columns(origin_ewkt: str | None, with_waypoints: bool) -> list[Any]:
    cols: list[Any] = [
        Route.id,
        Route.name,
        Route.description,
        Route.author_id,
        Route.duration,
        Route.distance,
        Route.is_public,
        Route.is_hidden,
        Route.views_count,
        Route.likes_count,
        Route.created_at,
        Route.updated_at,
        literal_column("ST_AsGeoJSON(routes.route_geometry)").label("geometry"),
        User.username.label("author_username"),
        User.avatar_url.label("author_avatar_url"),
    ]
    if with_waypoints:
        cols.append(Route.waypoints)
    if origin_ewkt is not None:
        cols.append(
            func.ST_Distance(Route.route_geometry, func.ST_GeogFromText(origin_ewkt)).label(
                "distance_from_user"
            )
        )
    return cols


def _row_to_dict(row: Any, *, simplify_tolerance: float | None) -> dict[str, Any]:
    m = row._mapping
    geometry = json.loads(m["geometry"])
    if simplify_tolerance:
        geometry["coordinates"] = simplify_coords(geometry["coordinates"], simplify_tolerance)
    item: dict[str, Any] = {
        "id": m["id"],
        "name": m["name"],
        "description": m["description"],
        "author": {
            "id": m["author_id"],
            "username": m["author_username"],
            "avatar_url": m["author_avatar_url"],
        },
        "duration": m["duration"],
        "distance": m["distance"],
        "is_public": m["is_public"],
        "is_hidden": m["is_hidden"],
        "views_count": m["views_count"],
        "likes_count": m["likes_count"],
        "geometry": geometry,
        "created_at": m["created_at"],
        "updated_at": m["updated_at"],
        "tags": [],
        "is_liked": False,
        "distance_from_user": None,
    }
    if "waypoints" in m:
        item["waypoints"] = m["waypoints"]
    if "distance_from_user" in m and m["distance_from_user"] is not None:
        item["distance_from_user"] = int(round(m["distance_from_user"]))
    return item


async def _attach_tags(session: AsyncSession, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    ids = [item["id"] for item in items]
    rows = await session.execute(
        select(RouteTag.route_id, Tag.id, Tag.name, Tag.color)
        .join(Tag, Tag.id == RouteTag.tag_id)
        .where(RouteTag.route_id.in_(ids))
        .order_by(Tag.name)
    )
    by_route: dict[int, list[dict[str, Any]]] = {}
    for route_id, tag_id, name, color in rows.all():
        by_route.setdefault(route_id, []).append({"id": tag_id, "name": name, "color": color})
    for item in items:
        item["tags"] = by_route.get(item["id"], [])


async def _attach_likes(session: AsyncSession, items: list[dict[str, Any]], user_id: int) -> None:
    if not items:
        return
    liked = await session.scalars(
        select(Like.route_id).where(
            Like.user_id == user_id, Like.route_id.in_([i["id"] for i in items])
        )
    )
    liked_ids = set(liked.all())
    for item in items:
        item["is_liked"] = item["id"] in liked_ids


def _jsonable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        copy = dict(item)
        for key in ("created_at", "updated_at"):
            if isinstance(copy[key], datetime):
                copy[key] = copy[key].isoformat()
        out.append(copy)
    return out


async def get_route_detail(
    session: AsyncSession, route_id: int, viewer: User | None
) -> dict[str, Any] | None:
    result = await session.execute(
        select(*_columns(None, True))
        .select_from(Route)
        .join(User, User.id == Route.author_id)
        .where(Route.id == route_id, read_visibility(viewer))
    )
    row = result.first()
    if row is None:
        return None
    item = _row_to_dict(row, simplify_tolerance=None)
    await _attach_tags(session, [item])
    if viewer is not None:
        await _attach_likes(session, [item], viewer.id)
    return item


@dataclass(slots=True)
class RouteFilters:
    lat: float | None = None
    lng: float | None = None
    radius: int | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    tags: list[str] | None = None
    tags_match: str = "any"
    sort: RouteSort = RouteSort.POPULAR
    page: int = 1
    limit: int = 20


def validate_filters(f: RouteFilters) -> None:
    if (f.lat is None) != (f.lng is None):
        raise UnprocessableError("lat and lng must be provided together", code="invalid_filters")
    if f.lat is None and f.radius is not None:
        raise UnprocessableError("radius requires lat and lng", code="invalid_filters")
    if f.lat is None and f.sort == RouteSort.DISTANCE:
        raise UnprocessableError("sort=distance requires lat and lng", code="invalid_filters")
    if (
        f.duration_min is not None
        and f.duration_max is not None
        and f.duration_min > f.duration_max
    ):
        raise UnprocessableError(
            "duration_min must not exceed duration_max", code="invalid_filters"
        )


async def _page_query(
    session: AsyncSession,
    *,
    conditions: list[ColumnElement[bool]],
    order_by: list[Any],
    page: int,
    limit: int,
    origin_ewkt: str | None,
    simplify_tolerance: float | None,
) -> tuple[list[dict[str, Any]], int]:
    total = await session.scalar(select(func.count()).select_from(Route).where(*conditions))
    result = await session.execute(
        select(*_columns(origin_ewkt, False))
        .select_from(Route)
        .join(User, User.id == Route.author_id)
        .where(*conditions)
        .order_by(*order_by)
        .limit(limit)
        .offset((page - 1) * limit)
    )
    items = [_row_to_dict(r, simplify_tolerance=simplify_tolerance) for r in result.all()]
    await _attach_tags(session, items)
    return items, int(total or 0)


def _order(sort: RouteSort) -> list[Any]:
    if sort == RouteSort.LIKES:
        return [desc(Route.likes_count), desc(Route.id)]
    if sort == RouteSort.VIEWS:
        return [desc(Route.views_count), desc(Route.id)]
    if sort == RouteSort.NEW:
        return [desc(Route.created_at), desc(Route.id)]
    if sort == RouteSort.DURATION:
        return [Route.duration.asc(), Route.id.asc()]
    # "popular" (default): likes weigh more than views
    return [desc(Route.likes_count * 5 + Route.views_count), desc(Route.created_at), desc(Route.id)]


async def list_public_routes(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    filters: RouteFilters,
    viewer: User | None,
) -> dict[str, Any]:
    validate_filters(filters)
    origin = None
    radius = None
    if filters.lat is not None and filters.lng is not None:
        origin = point_ewkt(filters.lat, filters.lng)
        radius = filters.radius or DEFAULT_RADIUS_M

    names = sorted({t.strip().lower() for t in filters.tags or [] if t.strip()})
    cache_key = None
    if settings.list_cache_ttl_seconds > 0:
        params = {
            "o": origin, "r": radius, "dmin": filters.duration_min, "dmax": filters.duration_max,
            "t": names, "m": filters.tags_match, "s": str(filters.sort),
            "p": filters.page, "l": filters.limit,
        }  # fmt: skip
        try:
            version = await redis.get(LIST_VERSION_KEY) or "0"
            digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            cache_key = f"{LIST_CACHE_PREFIX}{version}:{digest}"
            cached = await redis.get(cache_key)
        except RedisError:
            cache_key, cached = None, None
        if cached:
            try:
                payload = json.loads(cached)
                items, total = payload["items"], payload["total"]
                if viewer is not None:
                    await _attach_likes(session, items, viewer.id)
                return make_page(items, total, filters.page, filters.limit)
            except (ValueError, KeyError, TypeError):
                logger.warning("Corrupted list cache entry")

    conditions: list[ColumnElement[bool]] = [Route.is_public.is_(True), Route.is_hidden.is_(False)]
    if origin is not None:
        near = func.ST_DWithin(Route.route_geometry, func.ST_GeogFromText(origin), radius)
        conditions.append(near)
    if filters.duration_min is not None:
        conditions.append(Route.duration >= filters.duration_min)
    if filters.duration_max is not None:
        conditions.append(Route.duration <= filters.duration_max)
    if names:
        def has_tag(name_list: list[str]) -> ColumnElement[bool]:
            return (
                select(1)
                .select_from(RouteTag)
                .join(Tag, Tag.id == RouteTag.tag_id)
                .where(RouteTag.route_id == Route.id, func.lower(Tag.name).in_(name_list))
                .exists()
            )

        if filters.tags_match == "all":
            conditions.extend(has_tag([n]) for n in names)
        else:
            conditions.append(has_tag(names))

    order = _order(filters.sort)
    if filters.sort == RouteSort.DISTANCE and origin is not None:
        order = [
            func.ST_Distance(Route.route_geometry, func.ST_GeogFromText(origin)),
            Route.id,
        ]

    items, total = await _page_query(
        session,
        conditions=conditions,
        order_by=order,
        page=filters.page,
        limit=filters.limit,
        origin_ewkt=origin,
        simplify_tolerance=settings.list_geometry_tolerance_deg,
    )
    if cache_key is not None:
        try:
            await redis.set(
                cache_key,
                json.dumps({"items": _jsonable(items), "total": total}),
                ex=settings.list_cache_ttl_seconds,
            )
        except RedisError:
            logger.warning("Could not store routes list in cache")
    if viewer is not None:
        await _attach_likes(session, items, viewer.id)
    return make_page(items, total, filters.page, filters.limit)


async def list_user_routes(
    session: AsyncSession, settings: Settings, user: User, page: int, limit: int
) -> dict[str, Any]:
    items, total = await _page_query(
        session,
        conditions=[Route.author_id == user.id],
        order_by=[desc(Route.created_at), desc(Route.id)],
        page=page,
        limit=limit,
        origin_ewkt=None,
        simplify_tolerance=settings.list_geometry_tolerance_deg,
    )
    await _attach_likes(session, items, user.id)
    return make_page(items, total, page, limit)


# --------------------------------------------------------------------------- mutations


async def resolve_tag_ids(session: AsyncSession, tag_ids: list[int]) -> list[int]:
    unique = list(dict.fromkeys(tag_ids))
    if not unique:
        return []
    found = set((await session.scalars(select(Tag.id).where(Tag.id.in_(unique)))).all())
    missing = [t for t in unique if t not in found]
    if missing:
        raise UnprocessableError(
            f"Unknown tag ids: {missing}", code="unknown_tag", details={"tag_ids": missing}
        )
    return unique


def _waypoints_json(points: Sequence[tuple[float, float]]) -> list[dict[str, float]]:
    return [{"lat": lat, "lng": lng} for lat, lng in points]


def _geometry(built: BuiltRoute) -> WKTElement:
    return WKTElement(to_linestring_wkt(built.coordinates), srid=4326)


async def create_route(
    session: AsyncSession,
    redis: Redis,
    *,
    author: User,
    data: RouteCreate,
    built: BuiltRoute,
    tag_ids: list[int],
) -> int:
    route = Route(
        name=data.name,
        description=data.description,
        author_id=author.id,
        waypoints=_waypoints_json([(w.lat, w.lng) for w in data.waypoints]),
        route_geometry=_geometry(built),
        duration=built.duration_s,
        distance=built.distance_m,
        is_public=data.is_public,
    )
    session.add(route)
    await session.flush()
    session.add_all([RouteTag(route_id=route.id, tag_id=t) for t in tag_ids])
    await session.commit()
    await bump_list_version(redis)
    return route.id


async def update_route(
    session: AsyncSession,
    redis: Redis,
    route: Route,
    data: RouteUpdate,
    *,
    built: BuiltRoute | None,
    tag_ids: list[int] | None,
) -> None:
    if data.name is not None:
        route.name = data.name
    if data.description is not None:
        route.description = data.description
    if data.is_public is not None:
        route.is_public = data.is_public
    if built is not None and data.waypoints is not None:
        route.waypoints = _waypoints_json([(w.lat, w.lng) for w in data.waypoints])
        route.route_geometry = _geometry(built)
        route.duration = built.duration_s
        route.distance = built.distance_m
    if tag_ids is not None:
        await session.execute(delete(RouteTag).where(RouteTag.route_id == route.id))
        session.add_all([RouteTag(route_id=route.id, tag_id=t) for t in tag_ids])
    route.updated_at = datetime.now(UTC)
    await session.commit()
    await bump_list_version(redis)


async def delete_route(session: AsyncSession, redis: Redis, route: Route) -> None:
    await session.delete(route)
    await session.commit()
    await bump_list_version(redis)


async def get_route_orm(session: AsyncSession, route_id: int) -> Route | None:
    return await session.get(Route, route_id)


def can_view(route: Route, viewer: User | None) -> bool:
    if route.is_public and not route.is_hidden:
        return True
    if viewer is None:
        return False
    return viewer.role in (Role.MANAGER.value, Role.ADMIN.value) or route.author_id == viewer.id


# --------------------------------------------------------------------------- likes


async def _likes_count(session: AsyncSession, route_id: int) -> int:
    return int(await session.scalar(select(Route.likes_count).where(Route.id == route_id)) or 0)


async def like_route(session: AsyncSession, redis: Redis, user: User, route_id: int) -> dict:
    visible = await session.scalar(
        select(Route.id).where(Route.id == route_id, likeable_visibility(user))
    )
    if visible is None:
        raise NotFoundError("Route not found", code="route_not_found")
    try:
        inserted = await session.scalar(
            pg_insert(Like)
            .values(user_id=user.id, route_id=route_id)
            .on_conflict_do_nothing(index_elements=["user_id", "route_id"])
            .returning(Like.id)
        )
        if inserted is not None:
            await session.execute(
                update(Route)
                .where(Route.id == route_id)
                .values(likes_count=Route.likes_count + 1)
                .execution_options(synchronize_session=False)
            )
        await session.commit()
    except IntegrityError as exc:  # route deleted concurrently
        await session.rollback()
        raise NotFoundError("Route not found", code="route_not_found") from exc
    if inserted is not None:
        await bump_list_version(redis)
    return {"liked": True, "likes_count": await _likes_count(session, route_id)}


async def unlike_route(session: AsyncSession, redis: Redis, user: User, route_id: int) -> dict:
    exists = await session.scalar(select(Route.id).where(Route.id == route_id))
    if exists is None:
        raise NotFoundError("Route not found", code="route_not_found")
    deleted = await session.scalar(
        delete(Like)
        .where(Like.user_id == user.id, Like.route_id == route_id)
        .returning(Like.id)
        .execution_options(synchronize_session=False)
    )
    if deleted is not None:
        await session.execute(
            update(Route)
            .where(Route.id == route_id)
            .values(likes_count=func.greatest(Route.likes_count - 1, 0))
            .execution_options(synchronize_session=False)
        )
    await session.commit()
    if deleted is not None:
        await bump_list_version(redis)
    return {"liked": False, "likes_count": await _likes_count(session, route_id)}


async def set_hidden(
    session: AsyncSession, redis: Redis, route: Route, hidden: bool
) -> None:
    if route.is_hidden != hidden:
        route.is_hidden = hidden
        route.updated_at = datetime.now(UTC)
        await session.commit()
        await bump_list_version(redis)

