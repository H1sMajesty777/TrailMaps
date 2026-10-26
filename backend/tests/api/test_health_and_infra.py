import time

import jwt
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.db.models import User
from app.services.bootstrap import ensure_admin
from tests.conftest import make_client
from tests.helpers import login


async def test_liveness(client):
    r = await client.get("/api/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readiness_ok(client):
    r = await client.get("/api/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_readiness_degraded_when_redis_is_down(app, client):
    app.state.redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    r = await client.get("/api/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "redis": "unavailable"}


async def test_swagger_only_in_dev(app_factory):
    prod_like = await app_factory(environment="production")
    async with make_client(prod_like) as c:
        assert (await c.get("/api/docs")).status_code == 404
        assert (await c.get("/api/openapi.json")).status_code == 404
    dev = await app_factory(environment="dev")
    async with make_client(dev) as c:
        assert (await c.get("/api/docs")).status_code == 200
        assert (await c.get("/api/openapi.json")).status_code == 200


async def test_unknown_path_uses_error_envelope(client):
    r = await client.get("/api/nope")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert body["request_id"] == r.headers["X-Request-ID"]


async def test_method_not_allowed_envelope(client):
    r = await client.delete("/api/health/live")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


async def test_validation_error_envelope(client):
    r = await client.post("/api/auth/login", json={"email": "bad"})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "validation_error"
    assert {"loc", "message", "type"} <= set(error["details"][0])


async def test_security_headers_and_request_id(client):
    r = await client.get("/api/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert len(r.headers["X-Request-ID"]) >= 8


async def test_valid_request_id_is_propagated_and_invalid_one_replaced(client):
    r = await client.get("/api/health/live", headers={"X-Request-ID": "trace-12345678"})
    assert r.headers["X-Request-ID"] == "trace-12345678"
    r = await client.get("/api/health/live", headers={"X-Request-ID": "bad id with spaces!"})
    assert r.headers["X-Request-ID"] != "bad id with spaces!"


async def test_unhandled_exception_returns_500_without_leaking(app, client):
    async def boom():
        raise RuntimeError("secret internal detail")

    app.add_api_route("/api/_boom", boom)
    r = await client.get("/api/_boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text


async def test_cors_allows_only_configured_origins(app_factory):
    app = await app_factory(cors_origins="https://app.example.com")
    async with make_client(app) as c:
        ok = await c.get("/api/health/live", headers={"Origin": "https://app.example.com"})
        assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
        bad = await c.get("/api/health/live", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in bad.headers
        preflight = await c.options(
            "/api/routes",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200


async def test_bootstrap_admin_is_created_once(app_factory):
    app = await app_factory(
        bootstrap_admin_email="Root@Example.com", bootstrap_admin_password="Sup3rSecret!pw"
    )
    async with make_client(app) as c:
        tokens = await login(c, "root@example.com", "Sup3rSecret!pw")
        me = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.json()["role"] == "admin"
    # a second start (e.g. another replica) must not fail or duplicate the user
    await ensure_admin(app.state.session_factory, app.state.settings)
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_token_signed_with_other_secret_is_rejected(client):
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "1", "type": "access", "jti": "x" * 32, "iss": "trailmaps", "iat": now, "exp": now + 60},
        "another-secret-another-secret-another-secret",
        algorithm="HS256",
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
