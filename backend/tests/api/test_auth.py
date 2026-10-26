import time

import jwt
from sqlalchemy import update

from app.db.models import User
from tests.conftest import make_client
from tests.helpers import PASSWORD, create_user_in_db, login

REGISTER = {"email": "New.User@Example.com", "username": "newbie", "password": PASSWORD}


async def test_register_creates_user_without_leaking_secrets(client):
    r = await client.post("/api/auth/register", json=REGISTER)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new.user@example.com"
    assert body["role"] == "user" and body["is_active"] is True
    assert "password" not in r.text and "password_hash" not in r.text


async def test_register_duplicates_are_rejected_case_insensitively(client):
    assert (await client.post("/api/auth/register", json=REGISTER)).status_code == 201
    r = await client.post("/api/auth/register", json={**REGISTER, "username": "other"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"
    r = await client.post(
        "/api/auth/register", json={**REGISTER, "email": "x@example.com", "username": "NEWBIE"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "username_taken"


async def test_register_validation(client):
    for override in (
        {"password": "nodigitshere"},
        {"password": "short1"},
        {"username": "a"},
        {"email": "nope"},
    ):
        r = await client.post("/api/auth/register", json={**REGISTER, **override})
        assert r.status_code == 422, override


async def test_login_returns_token_pair_and_sets_last_login(client):
    await client.post("/api/auth/register", json=REGISTER)
    r = await client.post("/api/auth/login", json={"email": "NEW.user@example.com", "password": PASSWORD})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60 and tokens["refresh_expires_in"] == 30 * 86400
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200 and me.json()["last_login"] is not None


async def test_wrong_password_and_unknown_user_look_identical(client):
    await client.post("/api/auth/register", json=REGISTER)
    wrong = await client.post("/api/auth/login", json={"email": "new.user@example.com", "password": "Wrong123"})
    unknown = await client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "Wrong123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"]
    assert wrong.json()["error"]["code"] == "invalid_credentials"


async def test_login_is_locked_after_repeated_failures(app_factory):
    app = await app_factory(login_max_failures=3)
    async with make_client(app) as c:
        await create_user_in_db(app, email="lock@example.com", username="locky")
        bad = {"email": "lock@example.com", "password": "Wrong123"}
        for _ in range(3):
            assert (await c.post("/api/auth/login", json=bad)).status_code == 401
        good = {"email": "lock@example.com", "password": PASSWORD}
        r = await c.post("/api/auth/login", json=good)
        assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
        assert int(r.headers["Retry-After"]) >= 1


async def test_successful_login_resets_failure_counter(app_factory):
    app = await app_factory(login_max_failures=3)
    async with make_client(app) as c:
        await create_user_in_db(app, email="reset@example.com", username="resetty")
        bad = {"email": "reset@example.com", "password": "Wrong123"}
        for _ in range(2):
            await c.post("/api/auth/login", json=bad)
        await login(c, "reset@example.com")
        for _ in range(2):
            assert (await c.post("/api/auth/login", json=bad)).status_code == 401


async def test_disabled_account_cannot_login(app, client):
    await create_user_in_db(app, email="off@example.com", username="offline", is_active=False)
    r = await client.post("/api/auth/login", json={"email": "off@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"


async def test_me_requires_a_valid_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


async def test_expired_access_token(client, alice, settings):
    now = int(time.time())
    expired = jwt.encode(
        {
            "sub": str(alice.id),
            "type": "access",
            "jti": "e" * 32,
            "iss": settings.jwt_issuer,
            "iat": now - 120,
            "exp": now - 60,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


async def test_token_types_are_not_interchangeable(client, alice):
    r = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {alice.tokens['refresh_token']}"}
    )
    assert r.status_code == 401
    r = await client.post("/api/auth/refresh", json={"refresh_token": alice.tokens["access_token"]})
    assert r.status_code == 401


async def test_refresh_rotates_and_old_refresh_token_is_dead(client, alice):
    first = alice.tokens["refresh_token"]
    r = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != first
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200
    replay = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "token_revoked"


async def test_logout_revokes_access_and_refresh_tokens(client, alice):
    r = await client.post(
        "/api/auth/logout",
        json={"refresh_token": alice.tokens["refresh_token"]},
        headers=alice.headers,
    )
    assert r.status_code == 204
    me = await client.get("/api/auth/me", headers=alice.headers)
    assert me.status_code == 401 and me.json()["error"]["code"] == "token_revoked"
    refresh = await client.post("/api/auth/refresh", json={"refresh_token": alice.tokens["refresh_token"]})
    assert refresh.status_code == 401


async def test_logout_without_body_and_requires_auth(client, alice):
    assert (await client.post("/api/auth/logout")).status_code == 401
    assert (await client.post("/api/auth/logout", headers=alice.headers)).status_code == 204


async def test_blocking_a_user_takes_effect_immediately(app, client, alice):
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.id == alice.id).values(is_active=False))
        await session.commit()
    r = await client.get("/api/auth/me", headers=alice.headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"
    r = await client.post("/api/auth/refresh", json={"refresh_token": alice.tokens["refresh_token"]})
    assert r.status_code == 403
