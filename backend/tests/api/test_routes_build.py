import pytest

from tests.helpers import AMS_A, AMS_B

URL = "/api/routes/build"


async def test_build_returns_geometry_duration_distance(client, alice):
    r = await client.post(URL, json={"waypoints": [AMS_A, AMS_B]}, headers=alice.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["geometry"]["type"] == "LineString"
    assert len(body["geometry"]["coordinates"]) >= 2
    assert 300 < body["distance"] < 600
    assert body["duration"] == pytest.approx(body["distance"] / 1.4, abs=2)


async def test_build_with_many_waypoints(client, alice):
    points = [{"lat": 52.37 + i * 0.001, "lng": 4.89} for i in range(25)]
    r = await client.post(URL, json={"waypoints": points}, headers=alice.headers)
    assert r.status_code == 200


async def test_build_requires_auth(client):
    assert (await client.post(URL, json={"waypoints": [AMS_A, AMS_B]})).status_code == 401


@pytest.mark.parametrize(
    "waypoints",
    [
        [AMS_A],
        [],
        [AMS_A] * 26,
        [AMS_A, {"lat": 91, "lng": 4}],
        [AMS_A, {"lat": 52, "lng": -181}],
        [AMS_A, {"lat": "x", "lng": 4}],
        [AMS_A, {"lat": 52}],
    ],
)
async def test_build_validates_waypoints(client, alice, waypoints):
    r = await client.post(URL, json={"waypoints": waypoints}, headers=alice.headers)
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("mode", "status", "code"),
    [
        ("down", 503, "routing_unavailable"),
        ("timeout", 503, "routing_unavailable"),
        ("server_error", 503, "routing_unavailable"),
        ("garbage", 503, "routing_unavailable"),
        ("not_json", 503, "routing_unavailable"),
        ("no_route", 422, "route_not_found"),
        ("no_segment", 422, "route_not_found"),
    ],
)
async def test_osrm_failures_are_translated(client, alice, fake_osrm, mode, status, code):
    fake_osrm.mode = mode
    r = await client.post(URL, json={"waypoints": [AMS_A, AMS_B]}, headers=alice.headers)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


async def test_identical_requests_are_served_from_redis_cache(client, alice, fake_osrm):
    payload = {"waypoints": [AMS_A, AMS_B]}
    first = await client.post(URL, json=payload, headers=alice.headers)
    second = await client.post(URL, json=payload, headers=alice.headers)
    assert first.json() == second.json()
    assert fake_osrm.calls == 1
    await client.post(URL, json={"waypoints": [AMS_B, AMS_A]}, headers=alice.headers)
    assert fake_osrm.calls == 2


async def test_cache_survives_osrm_outage(client, alice, fake_osrm):
    payload = {"waypoints": [AMS_A, AMS_B]}
    assert (await client.post(URL, json=payload, headers=alice.headers)).status_code == 200
    fake_osrm.mode = "down"
    assert (await client.post(URL, json=payload, headers=alice.headers)).status_code == 200


async def test_degenerate_route_is_too_short(client, alice):
    r = await client.post(URL, json={"waypoints": [AMS_A, AMS_A]}, headers=alice.headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "route_too_short"


async def test_overlong_route_is_rejected(app_factory):
    from tests.conftest import make_client
    from tests.helpers import make_actor

    app = await app_factory(max_route_distance_m=100)
    async with make_client(app) as c:
        actor = await make_actor(app, c, "longwalker")
        r = await c.post(URL, json={"waypoints": [AMS_A, AMS_B]}, headers=actor.headers)
        assert r.status_code == 422 and r.json()["error"]["code"] == "route_too_long"
