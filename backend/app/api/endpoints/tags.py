from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, SessionDep, StaffUser
from app.core.errors import ConflictError, NotFoundError
from app.db.models import Tag
from app.schemas.tags import TagCreate, TagOut, TagUpdate

router = APIRouter(prefix="/tags", tags=["tags"])


async def _name_taken(session, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Tag.id).where(func.lower(Tag.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Tag.id != exclude_id)
    return await session.scalar(stmt) is not None


@router.get("", response_model=list[TagOut])
async def list_tags(session: SessionDep):
    return (await session.scalars(select(Tag).order_by(Tag.name))).all()


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(payload: TagCreate, _: StaffUser, session: SessionDep):
    if await _name_taken(session, payload.name):
        raise ConflictError("Tag already exists", code="tag_exists")
    tag = Tag(name=payload.name, color=payload.color.lower())
    session.add(tag)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Tag already exists", code="tag_exists") from exc
    return tag


@router.put("/{tag_id}", response_model=TagOut)
async def update_tag(tag_id: int, payload: TagUpdate, _: AdminUser, session: SessionDep):
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError("Tag not found", code="tag_not_found")
    if payload.name is not None:
        if await _name_taken(session, payload.name, exclude_id=tag.id):
            raise ConflictError("Tag already exists", code="tag_exists")
        tag.name = payload.name
    if payload.color is not None:
        tag.color = payload.color.lower()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Tag already exists", code="tag_exists") from exc
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: int, _: AdminUser, session: SessionDep) -> Response:
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError("Tag not found", code="tag_not_found")
    await session.delete(tag)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
