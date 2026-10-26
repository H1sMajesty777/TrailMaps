"""Route view counters: Redis buffer (deduplicated) flushed to PostgreSQL in batches."""

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Route, RouteView, User

logger = logging.getLogger(__name__)

VIEWS_QUEUE_KEY = "views:queue"


async def record_view(
    redis: Redis,
    settings: Settings,
    *,
    route_id: int,
    author_id: int,
    user_id: int | None,
    client_ip: str,
) -> None:
    """Runs as a FastAPI BackgroundTask. Never raises: counting views must not break reads."""
    if user_id is not None and user_id == author_id:
        return
    viewer = f"u{user_id}" if user_id is not None else f"ip:{client_ip}"
    try:
        first = await redis.set(
            f"view:{route_id}:{viewer}", "1", nx=True, ex=settings.view_dedup_window_seconds
        )
        if first:
            event = {"r": route_id, "u": user_id, "t": datetime.now(UTC).isoformat()}
            await redis.rpush(VIEWS_QUEUE_KEY, json.dumps(event))
    except RedisError:
        logger.warning("Could not record route view")


async def flush_views(
    redis: Redis, session_factory: async_sessionmaker[AsyncSession], batch_size: int = 1000
) -> int:
    """Move buffered view events to route_views and increment routes.views_count."""
    total = 0
    while True:
        raw = await redis.lpop(VIEWS_QUEUE_KEY, batch_size)
        if not raw:
            return total
        events = []
        for item in raw:
            try:
                data = json.loads(item)
                events.append(
                    (int(data["r"]), data["u"], datetime.fromisoformat(data["t"]))
                )
            except (ValueError, KeyError, TypeError):
                logger.warning("Dropping malformed view event")
        try:
            await _persist(session_factory, events)
        except Exception:
            await redis.rpush(VIEWS_QUEUE_KEY, *raw)  # put the batch back, retry later
            raise
        total += len(events)


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    events: list[tuple[int, int | None, datetime]],
) -> None:
    if not events:
        return
    async with session_factory() as session:
        route_ids = {e[0] for e in events}
        user_ids = {e[1] for e in events if e[1] is not None}
        existing_routes = set(
            (await session.scalars(select(Route.id).where(Route.id.in_(route_ids)))).all()
        )
        existing_users = (
            set((await session.scalars(select(User.id).where(User.id.in_(user_ids)))).all())
            if user_ids
            else set()
        )
        rows = []
        counts: Counter[int] = Counter()
        for route_id, user_id, viewed_at in events:
            if route_id not in existing_routes:
                continue  # route was deleted after the view was buffered
            rows.append(
                {
                    "route_id": route_id,
                    "user_id": user_id if user_id in existing_users else None,
                    "viewed_at": viewed_at,
                }
            )
            counts[route_id] += 1
        if rows:
            await session.execute(insert(RouteView), rows)
            for route_id, n in counts.items():
                await session.execute(
                    update(Route)
                    .where(Route.id == route_id)
                    .values(views_count=Route.views_count + n)
                    .execution_options(synchronize_session=False)
                )
        await session.commit()
