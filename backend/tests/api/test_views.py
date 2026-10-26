import pytest
from sqlalchemy import delete, func, select

from app.db.models import Route, RouteView, User
from app.services.views import VIEWS_QUEUE_KEY, flush_views
from tests.helpers import create_route


async def flush(app):
    return await flush_views(app.state.redis, app.state.session_factory)


async def view_rows(app, route_id=None):
    async with app.state.session_factory() as session:
        stmt = select(func.count()).select_from(RouteView)
        if route_id is not None:
            stmt = stmt.where(RouteView.route_id == route_id)
        return await session.scalar(stmt)


async def test_views_are_buffered_in_redis_and_flushed_in_batches(app, client, alice, bob, manager):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}"
    await client.get(url, headers=bob.headers)
    await client.get(url, headers=manager.headers)
    assert await app.state.redis.llen(VIEWS_QUEUE_KEY) == 2
    assert await view_rows(app) == 0  # nothing written to PostgreSQL yet
    assert await flush(app) == 2
    assert await app.state.redis.llen(VIEWS_QUEUE_KEY) == 0
    assert await view_rows(app, route["id"]) == 2
    assert (await client.get(url)).json()["views_count"] == 2


async def test_same_viewer_is_counted_once_per_window(app, client, alice, bob):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}"
    for _ in range(3):
        await client.get(url, headers=bob.headers)
    await flush(app)
    assert await view_rows(app, route["id"]) == 1


async def test_anonymous_views_are_deduplicated_by_ip_and_stored_without_user(app, client, alice):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}"
    await client.get(url)
    await client.get(url)
    await flush(app)
    async with app.state.session_factory() as session:
        rows = (await session.scalars(select(RouteView))).all()
    assert len(rows) == 1 and rows[0].user_id is None


async def test_authors_own_views_and_non_public_routes_are_not_counted(app, client, alice, bob):
    route = await create_route(client, alice)
    await client.get(f"/api/routes/{route['id']}", headers=alice.headers)
    private = await create_route(client, alice, is_public=False)
    await client.get(f"/api/routes/{private['id']}", headers=alice.headers)
    await client.get("/api/routes", headers=bob.headers)  # listing does not count either
    assert await app.state.redis.llen(VIEWS_QUEUE_KEY) == 0


async def test_views_for_deleted_routes_are_dropped_safely(app, client, alice, bob):
    gone = await create_route(client, alice, "gone")
    kept = await create_route(client, alice, "kept")
    await client.get(f"/api/routes/{gone['id']}", headers=bob.headers)
    await client.get(f"/api/routes/{kept['id']}", headers=bob.headers)
    await client.delete(f"/api/routes/{gone['id']}", headers=alice.headers)
    assert await flush(app) == 2  # both events consumed, one silently discarded
    assert await view_rows(app) == 1


async def test_failed_flush_returns_events_to_the_queue(app, client, alice, bob):
    route = await create_route(client, alice)
    await client.get(f"/api/routes/{route['id']}", headers=bob.headers)

    class Broken:
        def __call__(self):
            raise RuntimeError("database down")

    with pytest.raises(RuntimeError):
        await flush_views(app.state.redis, Broken())
    assert await app.state.redis.llen(VIEWS_QUEUE_KEY) == 1
    assert await flush(app) == 1
    assert await view_rows(app, route["id"]) == 1


async def test_flush_is_incremental_on_the_counter(app, client, alice, bob, manager):
    route = await create_route(client, alice)
    await client.get(f"/api/routes/{route['id']}", headers=bob.headers)
    await flush(app)
    await client.get(f"/api/routes/{route['id']}", headers=manager.headers)
    await flush(app)
    async with app.state.session_factory() as session:
        assert await session.scalar(select(Route.views_count).where(Route.id == route["id"])) == 2
    assert await flush(app) == 0


async def test_user_deletion_keeps_views_anonymised(app, client, alice, bob):
    route = await create_route(client, alice)
    await client.get(f"/api/routes/{route['id']}", headers=bob.headers)
    await flush(app)
    async with app.state.session_factory() as session:
        await session.execute(delete(User).where(User.id == bob.id))
        await session.commit()
        row = (await session.scalars(select(RouteView))).one()
    assert row.user_id is None


async def test_counter_update_does_not_touch_updated_at(app, client, alice, bob):
    route = await create_route(client, alice)
    async with app.state.session_factory() as session:
        before = await session.scalar(select(Route.updated_at).where(Route.id == route["id"]))
    await client.get(f"/api/routes/{route['id']}", headers=bob.headers)
    await flush(app)
    async with app.state.session_factory() as session:
        after = await session.scalar(select(Route.updated_at).where(Route.id == route["id"]))
    assert before == after
