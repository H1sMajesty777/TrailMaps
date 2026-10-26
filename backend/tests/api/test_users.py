import io
from pathlib import Path

from PIL import Image
from sqlalchemy import update

from app.db.models import Route, User
from tests.conftest import make_client
from tests.helpers import create_route, make_actor


def png_bytes(size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


async def test_get_and_update_profile(client, alice):
    r = await client.get("/api/users/me", headers=alice.headers)
    assert r.status_code == 200 and r.json()["username"] == "alice"
    r = await client.put(
        "/api/users/me",
        json={"bio": "I walk a lot", "phone": "+31 6 1234 5678", "username": "alice_w"},
        headers=alice.headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bio"] == "I walk a lot" and body["phone"] == "+31612345678"
    assert body["username"] == "alice_w"


async def test_phone_can_be_cleared(client, alice):
    await client.put("/api/users/me", json={"phone": "+31612345678"}, headers=alice.headers)
    r = await client.put("/api/users/me", json={"phone": ""}, headers=alice.headers)
    assert r.status_code == 200 and r.json()["phone"] is None


async def test_profile_update_validation_and_conflicts(client, alice, bob):
    assert (await client.put("/api/users/me", json={}, headers=alice.headers)).status_code == 422
    assert (
        await client.put("/api/users/me", json={"phone": "abc"}, headers=alice.headers)
    ).status_code == 422
    r = await client.put("/api/users/me", json={"username": "BOB"}, headers=alice.headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "username_taken"
    # changing only the letter case of your own name is fine
    r = await client.put("/api/users/me", json={"username": "ALICE"}, headers=alice.headers)
    assert r.status_code == 200


async def test_profile_requires_auth(client):
    assert (await client.get("/api/users/me")).status_code == 401
    assert (await client.put("/api/users/me", json={"bio": "x"})).status_code == 401


async def test_public_profile_hides_private_fields(client, alice, bob):
    r = await client.get(f"/api/users/{bob.id}", headers=alice.headers)
    assert r.status_code == 200
    assert set(r.json()) == {"id", "username", "avatar_url", "bio", "created_at"}
    assert (await client.get("/api/users/99999", headers=alice.headers)).status_code == 404
    assert (await client.get(f"/api/users/{bob.id}")).status_code == 401


async def test_blocked_user_profile_is_hidden_from_users_but_not_admins(app, client, alice, bob, admin):
    async with app.state.session_factory() as session:
        await session.execute(update(User).where(User.id == bob.id).values(is_active=False))
        await session.commit()
    assert (await client.get(f"/api/users/{bob.id}", headers=alice.headers)).status_code == 404
    assert (await client.get(f"/api/users/{bob.id}", headers=admin.headers)).status_code == 200


async def test_my_routes_include_private_and_hidden(app, client, alice, bob):
    await create_route(client, alice, "public one")
    private = await create_route(client, alice, "private one", is_public=False)
    hidden = await create_route(client, alice, "hidden one")
    await create_route(client, bob, "bobs")
    async with app.state.session_factory() as session:
        await session.execute(update(Route).where(Route.id == hidden["id"]).values(is_hidden=True))
        await session.commit()
    r = await client.get("/api/users/me/routes", headers=alice.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert {i["name"] for i in body["items"]} == {"public one", "private one", "hidden one"}
    assert private["is_public"] is False


async def test_avatar_upload_normalizes_image_and_stores_file(client, alice, settings):
    r = await client.post(
        "/api/users/me/avatar",
        files={"file": ("me.png", png_bytes(), "image/png")},
        headers=alice.headers,
    )
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith("/uploads/avatars/") and url.endswith(".webp")
    stored = Path(settings.upload_dir) / "avatars" / url.rsplit("/", 1)[-1]
    assert stored.exists()
    with Image.open(stored) as img:
        assert img.format == "WEBP" and img.size == (settings.avatar_size_px,) * 2


async def test_avatar_replacement_removes_old_file(client, alice, settings):
    first = await client.post(
        "/api/users/me/avatar", files={"file": ("a.png", png_bytes(), "image/png")}, headers=alice.headers
    )
    second = await client.post(
        "/api/users/me/avatar", files={"file": ("b.png", png_bytes((50, 50)), "image/png")}, headers=alice.headers
    )
    avatars = Path(settings.upload_dir) / "avatars"
    old = avatars / first.json()["avatar_url"].rsplit("/", 1)[-1]
    new = avatars / second.json()["avatar_url"].rsplit("/", 1)[-1]
    assert not old.exists() and new.exists()


async def test_avatar_rejects_non_images(client, alice):
    r = await client.post(
        "/api/users/me/avatar",
        files={"file": ("evil.png", b"<?php echo 1; ?>", "image/png")},
        headers=alice.headers,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_image"
    r = await client.post(
        "/api/users/me/avatar", files={"file": ("e.png", b"", "image/png")}, headers=alice.headers
    )
    assert r.status_code == 422


async def test_avatar_size_limit(app_factory):
    app = await app_factory(avatar_max_bytes=1000)
    async with make_client(app) as c:
        actor = await make_actor(app, c, "bigfile")
        r = await c.post(
            "/api/users/me/avatar",
            files={"file": ("big.png", b"0" * 5000, "image/png")},
            headers=actor.headers,
        )
        assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


async def test_avatar_requires_auth_and_file(client, alice):
    files = {"file": ("a.png", png_bytes(), "image/png")}
    assert (await client.post("/api/users/me/avatar", files=files)).status_code == 401
    assert (await client.post("/api/users/me/avatar", headers=alice.headers)).status_code == 422
