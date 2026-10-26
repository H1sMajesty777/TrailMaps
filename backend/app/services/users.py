import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.db.models import User
from app.schemas.users import UserUpdate

logger = logging.getLogger(__name__)


async def update_profile(session: AsyncSession, user: User, data: UserUpdate) -> User:
    fields = data.model_fields_set
    if "username" in fields and data.username is not None:
        taken = await session.scalar(
            select(User.id).where(
                func.lower(User.username) == data.username.lower(), User.id != user.id
            )
        )
        if taken is not None:
            raise ConflictError("Username is already taken", code="username_taken")
        user.username = data.username
    if "bio" in fields:
        user.bio = data.bio or ""
    if "phone" in fields:
        user.phone = data.phone
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Username is already taken", code="username_taken") from exc
    return user
