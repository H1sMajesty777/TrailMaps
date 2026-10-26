from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.services.views import flush_views
from tests.helpers import create_route

OVERVIEW = "/api/admin/stats"
USERS = "/api/admin/stats/users"
VIEWS = "/api/admin/stats/views"


def today():
    return datetime.now(UTC).date()


async def test_overview_is_admin_only(client, alice, manager, admin):
    assert (await client.get(OVERVIEW)).status_code == 401
    assert (await client.get(OVERVIEW, headers=alice.headers)).status_code == 403
    assert (await client.get(OVERVIEW, headers=manager.headers)).status_code == 403
    assert (await client.get(OVERVIEW, headers=admin.headers)).status_code == 200


async def test_overview_numbers(app, client, alice, bob, manager, admin):
    r1 = await create_route(client, alice, "one")
    await create_route(client, alice, "two", is_public=False)
    await client.post(f"/api/routes/{r1['id']}/like", headers=bob.headers)
    await client.get(f"/api/routes/{r1['id']}", headers=bob.headers)
    await flush_views(app.state.redis, app.state.session_factory)
    await client.post(
        "/api/complaints", json={"route_id": r1["id"], "description": "spam route"}, headers=bob.headers
    )
    await client.put(f"/api/admin/routes/{r1['id']}/visibility", json={"is_hidden": True}, headers=admin.headers)
    async with app.state.session_factory() as session:
        await session.execute(text("UPDATE users SET is_active = false WHERE username = 'bob'"))
        await session.commit()

    body = (await client.get(OVERVIEW, headers=admin.headers)).json()
    assert body["users"] == {
        "total": 4,
        "active": 3,
        "blocked": 1,
        "by_role": {"user": 2, "manager": 1, "admin": 1},
    }
    assert body["routes"] == {"total": 2, "public": 1, "hidden": 1}
    assert body["complaints"] == {"pending": 1, "resolved": 0, "rejected": 0}
    assert body["likes_total"] == 1 and body["views_total"] == 1
    assert body["generated_at"]


async def test_users_stats_default_period_is_last_30_days(client, alice, bob, manager):
    r = await client.get(USERS, headers=manager.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["granularity"] == "day" and body["date_to"] == today().isoformat()
    assert body["date_from"] == (today() - timedelta(days=29)).isoformat()
    assert len(body["series"]) == 30 and body["total"] == 3
    assert body["series"][-1] == {"period": today().isoformat(), "count": 3}
    assert all(p["count"] == 0 for p in body["series"][:-1])


async def test_users_stats_respects_range_and_backdated_users(app, client, manager):
    async with app.state.session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (email, username, password_hash, created_at) VALUES "
                "('old1@example.com','old1','x', now() - interval '10 days'),"
                "('old2@example.com','old2','x', now() - interval '10 days'),"
                "('ancient@example.com','ancient','x', now() - interval '200 days')"
            )
        )
        await session.commit()
    day = (today() - timedelta(days=10)).isoformat()
    r = await client.get(USERS, params={"date_from": day, "date_to": day}, headers=manager.headers)
    assert r.json()["series"] == [{"period": day, "count": 2}]
    month = await client.get(
        USERS,
        params={"date_from": (today() - timedelta(days=400)).isoformat(), "granularity": "month"},
        headers=manager.headers,
    )
    assert month.status_code == 200 and month.json()["total"] == 4  # incl. the manager itself
    assert all(p["period"].endswith("-01") for p in month.json()["series"])


async def test_week_granularity_buckets_start_on_monday(client, manager):
    r = await client.get(USERS, params={"granularity": "week"}, headers=manager.headers)
    assert r.status_code == 200
    series = r.json()["series"]
    assert all(datetime.fromisoformat(p["period"]).weekday() == 0 for p in series)
    assert sum(p["count"] for p in series) == 1


async def test_views_stats_and_top_routes(app, client, alice, bob, manager):
    popular = await create_route(client, alice, "popular")
    quiet = await create_route(client, alice, "quiet")
    for viewer in (bob, manager):
        await client.get(f"/api/routes/{popular['id']}", headers=viewer.headers)
    await client.get(f"/api/routes/{quiet['id']}", headers=bob.headers)
    await flush_views(app.state.redis, app.state.session_factory)

    r = await client.get(VIEWS, headers=manager.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and body["series"][-1]["count"] == 3
    assert body["top_routes"] == [
        {"route_id": popular["id"], "name": "popular", "views": 2},
        {"route_id": quiet["id"], "name": "quiet", "views": 1},
    ]


async def test_stats_permissions(client, alice, manager, admin):
    for url in (USERS, VIEWS):
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=alice.headers)).status_code == 403
        assert (await client.get(url, headers=manager.headers)).status_code == 200
        assert (await client.get(url, headers=admin.headers)).status_code == 200


async def test_invalid_periods_are_rejected(client, manager):
    d = today()
    cases = [
        {"date_from": d.isoformat(), "date_to": (d - timedelta(days=1)).isoformat()},
        {"date_from": (d - timedelta(days=400)).isoformat(), "granularity": "day"},
        {"date_from": (d - timedelta(days=5000)).isoformat(), "granularity": "month"},
        {"date_from": "not-a-date"},
        {"granularity": "year"},
    ]
    for params in cases:
        r = await client.get(USERS, params=params, headers=manager.headers)
        assert r.status_code == 422, params
