from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.models import Role, User
from app.schemas.admin import AdminUserUpdate
from app.schemas.common import make_page


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_users(
    session: AsyncSession,
    *,
    q: str | None,
    role: Role | None,
    is_active: bool | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    conditions = []
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(User.email.ilike(pattern, escape="\\"), User.username.ilike(pattern, escape="\\"))
        )
    if role is not None:
        conditions.append(User.role == role.value)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    total = await session.scalar(select(func.count()).select_from(User).where(*conditions))
    users = await session.scalars(
        select(User).where(*conditions).order_by(User.id).limit(limit).offset((page - 1) * limit)
    )
    return make_page(list(users.all()), int(total or 0), page, limit)


async def update_user(
    session: AsyncSession, actor: User, user_id: int, data: AdminUserUpdate
) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("User not found", code="user_not_found")
    if target.id == actor.id:
        # prevents an admin from locking the system out of its last administrator
        raise ConflictError("You cannot change your own role or status", code="self_modification")
    if data.role is not None:
        target.role = data.role.value
    if data.is_active is not None:
        target.is_active = data.is_active
    await session.commit()
    return target
