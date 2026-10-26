import asyncio

from sqlalchemy import func, select, update

from app.db.models import Like, Route, User
from tests.helpers import create_route


async def count_likes(app, route_id):
    async with app.state.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Like).where(Like.route_id == route_id))


async def test_like_and_unlike_are_idempotent(app, client, alice, bob):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}/like"
    r = await client.post(url, headers=bob.headers)
    assert r.status_code == 200 and r.json() == {"liked": True, "likes_count": 1}
    again = await client.post(url, headers=bob.headers)
    assert again.json() == {"liked": True, "likes_count": 1}
    assert await count_likes(app, route["id"]) == 1
    r = await client.delete(url, headers=bob.headers)
    assert r.json() == {"liked": False, "likes_count": 0}
    again = await client.delete(url, headers=bob.headers)
    assert again.status_code == 200 and again.json()["likes_count"] == 0


async def test_counter_matches_number_of_distinct_users(client, alice, bob, manager):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}/like"
    for actor in (alice, bob, manager):
        await client.post(url, headers=actor.headers)
    detail = (await client.get(f"/api/routes/{route['id']}")).json()
    assert detail["likes_count"] == 3
    await client.delete(url, headers=bob.headers)
    assert (await client.get(f"/api/routes/{route['id']}")).json()["likes_count"] == 2


async def test_concurrent_likes_from_one_user_count_once(app, client, alice, bob):
    route = await create_route(client, alice)
    url = f"/api/routes/{route['id']}/like"
    responses = await asyncio.gather(*[client.post(url, headers=bob.headers) for _ in range(6)])
    assert all(r.status_code == 200 for r in responses)
    assert await count_likes(app, route["id"]) == 1
    assert (await client.get(f"/api/routes/{route['id']}")).json()["likes_count"] == 1


async def test_like_requires_auth_and_existing_visible_route(app, client, alice, bob):
    route = await create_route(client, alice)
    assert (await client.post(f"/api/routes/{route['id']}/like")).status_code == 401
    assert (await client.post("/api/routes/99999/like", headers=bob.headers)).status_code == 404
    assert (await client.delete("/api/routes/99999/like", headers=bob.headers)).status_code == 404
    private = await create_route(client, alice, is_public=False)
    assert (await client.post(f"/api/routes/{private['id']}/like", headers=bob.headers)).status_code == 404
    assert (await client.post(f"/api/routes/{private['id']}/like", headers=alice.headers)).status_code == 200


async def test_hidden_routes_cannot_be_liked_by_others(app, client, alice, bob):
    route = await create_route(client, alice)
    async with app.state.session_factory() as session:
        await session.execute(update(Route).where(Route.id == route["id"]).values(is_hidden=True))
        await session.commit()
    assert (await client.post(f"/api/routes/{route['id']}/like", headers=bob.headers)).status_code == 404


async def test_deleting_a_user_cascades_likes(app, client, alice, bob):
    route = await create_route(client, alice)
    await client.post(f"/api/routes/{route['id']}/like", headers=bob.headers)
    assert await count_likes(app, route["id"]) == 1
    async with app.state.session_factory() as session:
        await session.delete(await session.get(User, bob.id))
        await session.commit()
    assert await count_likes(app, route["id"]) == 0
