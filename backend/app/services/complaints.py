import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.db.models import Complaint, ComplaintStatus, Role, Route, User
from app.schemas.common import make_page
from app.schemas.complaints import ComplaintCreate, ComplaintUpdate
from app.services import routes as routes_service

logger = logging.getLogger(__name__)


def _select_complaints():
    return (
        select(
            Complaint.id,
            Complaint.route_id,
            Route.name.label("route_name"),
            Complaint.user_id,
            User.username,
            Complaint.description,
            Complaint.status,
            Complaint.created_at,
        )
        .join(Route, Route.id == Complaint.route_id)
        .join(User, User.id == Complaint.user_id)
    )


async def get_complaint_out(session: AsyncSession, complaint_id: int) -> dict[str, Any]:
    row = (
        await session.execute(_select_complaints().where(Complaint.id == complaint_id))
    ).first()
    if row is None:
        raise NotFoundError("Complaint not found", code="complaint_not_found")
    return dict(row._mapping)


async def create_complaint(
    session: AsyncSession, user: User, data: ComplaintCreate
) -> dict[str, Any]:
    route = await session.scalar(
        select(Route).where(Route.id == data.route_id, routes_service.read_visibility(user))
    )
    if route is None:
        raise NotFoundError("Route not found", code="route_not_found")
    if route.author_id == user.id:
        raise UnprocessableError("You cannot report your own route", code="own_route")
    duplicate = await session.scalar(
        select(Complaint.id).where(
            Complaint.user_id == user.id,
            Complaint.route_id == route.id,
            Complaint.status == ComplaintStatus.PENDING.value,
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "You already have an open complaint for this route", code="complaint_already_pending"
        )
    complaint = Complaint(user_id=user.id, route_id=route.id, description=data.description)
    session.add(complaint)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "You already have an open complaint for this route", code="complaint_already_pending"
        ) from exc
    return await get_complaint_out(session, complaint.id)


async def list_complaints(
    session: AsyncSession,
    *,
    status: ComplaintStatus | None,
    route_id: int | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    conditions = []
    if status is not None:
        conditions.append(Complaint.status == status.value)
    if route_id is not None:
        conditions.append(Complaint.route_id == route_id)
    total = await session.scalar(select(func.count()).select_from(Complaint).where(*conditions))
    rows = await session.execute(
        _select_complaints()
        .where(*conditions)
        .order_by(Complaint.created_at.desc(), Complaint.id.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    )
    return make_page([dict(r._mapping) for r in rows.all()], int(total or 0), page, limit)


async def update_complaint(
    session: AsyncSession,
    redis: Redis,
    actor: User,
    complaint_id: int,
    data: ComplaintUpdate,
) -> dict[str, Any]:
    if data.hide_route is not None and actor.role != Role.ADMIN.value:
        raise ForbiddenError("Only administrators can hide routes", code="insufficient_role")
    complaint = await session.get(Complaint, complaint_id)
    if complaint is None:
        raise NotFoundError("Complaint not found", code="complaint_not_found")

    complaint.status = data.status.value
    route_to_hide = None
    if data.hide_route is not None:
        route_to_hide = await session.get(Route, complaint.route_id)
    try:
        await session.commit()
    except IntegrityError as exc:  # reopening would violate the one-pending-per-user rule
        await session.rollback()
        raise ConflictError(
            "Another open complaint from this user exists for the route",
            code="complaint_already_pending",
        ) from exc
    if route_to_hide is not None and data.hide_route is not None:
        await routes_service.set_hidden(session, redis, route_to_hide, data.hide_route)
    return await get_complaint_out(session, complaint_id)
