from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, RedisDep, SessionDep, StaffUser
from app.api.limits import rate_limit_user
from app.db.models import ComplaintStatus
from app.schemas.common import Page
from app.schemas.complaints import ComplaintCreate, ComplaintOut, ComplaintUpdate
from app.services import complaints as svc

router = APIRouter(prefix="/complaints", tags=["complaints"])


@router.post(
    "",
    response_model=ComplaintOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_user("complaint", "rate_limit_expensive_per_minute"))],
)
async def create_complaint(payload: ComplaintCreate, user: CurrentUser, session: SessionDep):
    return await svc.create_complaint(session, user, payload)


@router.get("", response_model=Page[ComplaintOut])
async def list_complaints(
    _: StaffUser,
    session: SessionDep,
    status_filter: Annotated[ComplaintStatus | None, Query(alias="status")] = None,
    route_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await svc.list_complaints(
        session, status=status_filter, route_id=route_id, page=page, limit=limit
    )


@router.put("/{complaint_id}", response_model=ComplaintOut)
async def update_complaint(
    complaint_id: int,
    payload: ComplaintUpdate,
    actor: StaffUser,
    session: SessionDep,
    redis: RedisDep,
):
    return await svc.update_complaint(session, redis, actor, complaint_id, payload)
