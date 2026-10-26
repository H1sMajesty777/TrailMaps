"""Create the first administrator from environment variables (idempotent)."""

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import hash_password_async
from app.db.models import Role, User

logger = logging.getLogger(__name__)


async def ensure_admin(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if not email or not password:
        return
    async with session_factory() as session:
        exists = await session.scalar(select(User.id).where(User.email == email))
        if exists is not None:
            return
        username_taken = await session.scalar(
            select(User.id).where(
                func.lower(User.username) == settings.bootstrap_admin_username.lower()
            )
        )
        if username_taken is not None:
            logger.warning("Bootstrap admin skipped: username is already taken")
            return
        session.add(
            User(
                email=email,
                username=settings.bootstrap_admin_username,
                password_hash=await hash_password_async(password, settings.bcrypt_rounds),
                role=Role.ADMIN.value,
            )
        )
        try:
            await session.commit()
            logger.info("Bootstrap administrator created")
        except IntegrityError:
            await session.rollback()  # another worker won the race
