from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.core.geo import iter_buckets
from app.db.models import Complaint, Like, Role, Route, RouteView, User
from app.schemas.admin import Granularity

MAX_DAILY_RANGE_DAYS = 366
MAX_RANGE_DAYS = 3660


def resolve_period(
    date_from: date | None, date_to: date | None, granularity: Granularity
) -> tuple[date, date]:
    end = date_to or datetime.now(UTC).date()
    start = date_from or (end - timedelta(days=29))
    if start > end:
        raise UnprocessableError("date_from must not be after date_to", code="invalid_period")
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS or (granularity == Granularity.DAY and span > MAX_DAILY_RANGE_DAYS):
        raise UnprocessableError(
            "Requested period is too long for this granularity", code="invalid_period"
        )
    return start, end


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(start, time.min, tzinfo=UTC),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC),
    )


async def _series(
    session: AsyncSession,
    *,
    table: str,
    column: str,
    start: date,
    end: date,
    granularity: Granularity,
) -> list[dict[str, Any]]:
    lower, upper = _bounds(start, end)
    # table/column/granularity come from code constants and a validated enum — never from input
    sql = text(
        f"SELECT date_trunc('{granularity.value}', {column} AT TIME ZONE 'UTC')::date AS bucket, "
        f"count(*) AS cnt FROM {table} WHERE {column} >= :lower AND {column} < :upper "
        "GROUP BY 1 ORDER BY 1"
    )
    rows = (await session.execute(sql, {"lower": lower, "upper": upper})).all()
    counts = {row.bucket: int(row.cnt) for row in rows}
    return [
        {"period": b, "count": counts.get(b, 0)}
        for b in iter_buckets(start, end, granularity.value)
    ]


async def users_stats(
    session: AsyncSession, start: date, end: date, granularity: Granularity
) -> dict[str, Any]:
    series = await _series(
        session, table="users", column="created_at", start=start, end=end, granularity=granularity
    )
    return {
        "granularity": granularity,
        "date_from": start,
        "date_to": end,
        "total": sum(p["count"] for p in series),
        "series": series,
    }


async def views_stats(
    session: AsyncSession, start: date, end: date, granularity: Granularity
) -> dict[str, Any]:
    series = await _series(
        session,
        table="route_views",
        column="viewed_at",
        start=start,
        end=end,
        granularity=granularity,
    )
    lower, upper = _bounds(start, end)
    views = func.count().label("views")
    top = await session.execute(
        select(RouteView.route_id, Route.name, views)
        .join(Route, Route.id == RouteView.route_id)
        .where(RouteView.viewed_at >= lower, RouteView.viewed_at < upper)
        .group_by(RouteView.route_id, Route.name)
        .order_by(views.desc(), RouteView.route_id)
        .limit(10)
    )
    return {
        "granularity": granularity,
        "date_from": start,
        "date_to": end,
        "total": sum(p["count"] for p in series),
        "series": series,
        "top_routes": [
            {"route_id": r.route_id, "name": r.name, "views": int(r.views)} for r in top.all()
        ],
    }


async def overview(session: AsyncSession) -> dict[str, Any]:
    u = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(User.is_active.is_(True)).label("active"),
                func.count().filter(User.role == Role.USER.value).label("user"),
                func.count().filter(User.role == Role.MANAGER.value).label("manager"),
                func.count().filter(User.role == Role.ADMIN.value).label("admin"),
            )
        )
    ).one()
    r = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Route.is_public.is_(True)).label("public"),
                func.count().filter(Route.is_hidden.is_(True)).label("hidden"),
            )
        )
    ).one()
    complaint_rows = (
        await session.execute(select(Complaint.status, func.count()).group_by(Complaint.status))
    ).all()
    by_status = {status: int(n) for status, n in complaint_rows}
    likes_total = await session.scalar(select(func.count()).select_from(Like))
    views_total = await session.scalar(select(func.count()).select_from(RouteView))
    return {
        "users": {
            "total": u.total,
            "active": u.active,
            "blocked": u.total - u.active,
            "by_role": {"user": u.user, "manager": u.manager, "admin": u.admin},
        },
        "routes": {"total": r.total, "public": r.public, "hidden": r.hidden},
        "complaints": {
            "pending": by_status.get("pending", 0),
            "resolved": by_status.get("resolved", 0),
            "rejected": by_status.get("rejected", 0),
        },
        "likes_total": int(likes_total or 0),
        "views_total": int(views_total or 0),
        "generated_at": datetime.now(UTC),
    }
