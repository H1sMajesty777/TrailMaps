from tests.conftest import make_client
from tests.helpers import AMS_A, AMS_B, make_actor


async def test_global_limit_returns_429_with_retry_after(app_factory):
    app = await app_factory(rate_limit_global_per_minute=3)
    async with make_client(app) as c:
        for _ in range(3):
            assert (await c.get("/api/tags")).status_code == 200
        r = await c.get("/api/tags")
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1
        assert r.json()["error"]["code"] == "too_many_requests"
        # health probes are exempt so orchestrators never get throttled
        assert (await c.get("/api/health/live")).status_code == 200


async def test_auth_endpoints_have_a_stricter_limit(app_factory):
    app = await app_factory(rate_limit_auth_per_minute=2)
    async with make_client(app) as c:
        body = {"email": "nobody@example.com", "password": "whatever1"}
        assert (await c.post("/api/auth/login", json=body)).status_code == 401
        assert (await c.post("/api/auth/login", json=body)).status_code == 401
        r = await c.post("/api/auth/login", json=body)
        assert r.status_code == 429 and "Retry-After" in r.headers


async def test_expensive_endpoints_are_limited_per_user(app_factory):
    app = await app_factory(rate_limit_expensive_per_minute=2)
    async with make_client(app) as c:
        a = await make_actor(app, c, "limited1")
        b = await make_actor(app, c, "limited2")
        payload = {"waypoints": [AMS_A, AMS_B]}
        for _ in range(2):
            assert (await c.post("/api/routes/build", json=payload, headers=a.headers)).status_code == 200
        r = await c.post("/api/routes/build", json=payload, headers=a.headers)
        assert r.status_code == 429
        # another user is not affected
        assert (await c.post("/api/routes/build", json=payload, headers=b.headers)).status_code == 200


async def test_limiter_can_be_disabled(app_factory):
    app = await app_factory(rate_limit_enabled=False, rate_limit_global_per_minute=1)
    async with make_client(app) as c:
        for _ in range(5):
            assert (await c.get("/api/tags")).status_code == 200
