from datetime import datetime

import pytest
from sqlalchemy import update

from app.db.models import Route
from tests.helpers import AMS_A, AMS_B, UTR_A, UTR_B, UTR_C, create_route, route_payload


async def make_tag(client, staff, name):
    r = await client.post("/api/tags", json={"name": name}, headers=staff.headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_route_computes_geometry_server_side(client, alice, manager):
    food = await make_tag(client, manager, "food")
    r = await client.post(
        "/api/routes",
        json=route_payload("Canal walk", description="Nice", tag_ids=[food["id"]]),
        headers=alice.headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Canal walk" and body["description"] == "Nice"
    assert body["author"] == {"id": alice.id, "username": "alice", "avatar_url": ""}
    assert body["tags"] == [food]
    assert body["waypoints"] == [AMS_A, AMS_B]
    assert body["geometry"]["type"] == "LineString" and len(body["geometry"]["coordinates"]) >= 3
    assert 300 < body["distance"] < 600 and body["duration"] > 0
    assert body["likes_count"] == 0 and body["views_count"] == 0
    assert body["is_public"] is True and body["is_hidden"] is False


async def test_client_supplied_metrics_are_ignored(client, alice):
    payload = route_payload("Sneaky", distance=1, duration=1, likes_count=999, is_hidden=True)
    body = (await client.post("/api/routes", json=payload, headers=alice.headers)).json()
    assert body["distance"] > 100 and body["likes_count"] == 0 and body["is_hidden"] is False


async def test_create_requires_auth_and_valid_input(client, alice):
    assert (await client.post("/api/routes", json=route_payload())).status_code == 401
    for bad in (
        route_payload(name="   "),
        route_payload(waypoints=[AMS_A]),
        route_payload(tag_ids=list(range(1, 12))),
        {"name": "x"},
    ):
        assert (await client.post("/api/routes", json=bad, headers=alice.headers)).status_code == 422


async def test_unknown_tag_is_rejected(client, alice):
    r = await client.post("/api/routes", json=route_payload(tag_ids=[4242]), headers=alice.headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_tag"


async def test_osrm_outage_blocks_creation_without_side_effects(client, alice, fake_osrm):
    fake_osrm.mode = "down"
    r = await client.post("/api/routes", json=route_payload(), headers=alice.headers)
    assert r.status_code == 503
    mine = await client.get("/api/users/me/routes", headers=alice.headers)
    assert mine.json()["total"] == 0


async def test_public_route_is_readable_anonymously(client, alice):
    created = await create_route(client, alice)
    r = await client.get(f"/api/routes/{created['id']}")
    assert r.status_code == 200 and r.json()["id"] == created["id"]
    assert (await client.get("/api/routes/99999")).status_code == 404
    assert (await client.get("/api/routes/abc")).status_code == 422


async def test_private_route_is_visible_to_author_and_staff_only(client, alice, bob, manager, admin):
    created = await create_route(client, alice, is_public=False)
    url = f"/api/routes/{created['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.get(url, headers=bob.headers)).status_code == 404
    assert (await client.get(url, headers=alice.headers)).status_code == 200
    assert (await client.get(url, headers=manager.headers)).status_code == 200
    assert (await client.get(url, headers=admin.headers)).status_code == 200


async def test_hidden_route_is_invisible_to_others_but_not_author(app, client, alice, bob):
    created = await create_route(client, alice)
    async with app.state.session_factory() as session:
        await session.execute(update(Route).where(Route.id == created["id"]).values(is_hidden=True))
        await session.commit()
    url = f"/api/routes/{created['id']}"
    assert (await client.get(url)).status_code == 404
    assert (await client.get(url, headers=bob.headers)).status_code == 404
    mine = await client.get(url, headers=alice.headers)
    assert mine.status_code == 200 and mine.json()["is_hidden"] is True


async def test_author_cannot_unhide_own_route(app, client, alice):
    created = await create_route(client, alice)
    async with app.state.session_factory() as session:
        await session.execute(update(Route).where(Route.id == created["id"]).values(is_hidden=True))
        await session.commit()
    r = await client.put(
        f"/api/routes/{created['id']}", json={"name": "renamed", "is_hidden": False}, headers=alice.headers
    )
    assert r.status_code == 200 and r.json()["is_hidden"] is True


async def test_update_metadata_and_tags(client, alice, manager):
    a, b = await make_tag(client, manager, "a"), await make_tag(client, manager, "b")
    created = await create_route(client, alice, tag_ids=[a["id"]])
    r = await client.put(
        f"/api/routes/{created['id']}",
        json={"name": "New name", "description": "d", "is_public": False, "tag_ids": [b["id"]]},
        headers=alice.headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "New name" and body["is_public"] is False
    assert [t["name"] for t in body["tags"]] == ["b"]
    assert body["distance"] == created["distance"]  # geometry untouched
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(created["updated_at"])
    cleared = await client.put(f"/api/routes/{created['id']}", json={"tag_ids": []}, headers=alice.headers)
    assert cleared.json()["tags"] == []


async def test_update_waypoints_rebuilds_geometry(client, alice):
    created = await create_route(client, alice)
    r = await client.put(
        f"/api/routes/{created['id']}",
        json={"waypoints": [UTR_A, UTR_B, UTR_C]},
        headers=alice.headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["distance"] > 2000 and body["distance"] != created["distance"]
    assert body["waypoints"] == [UTR_A, UTR_B, UTR_C]


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"waypoints": [AMS_A]}, {"name": ""}])
async def test_update_validation(client, alice, payload):
    created = await create_route(client, alice)
    r = await client.put(f"/api/routes/{created['id']}", json=payload, headers=alice.headers)
    assert r.status_code == 422


async def test_update_permissions(client, alice, bob, manager, admin):
    created = await create_route(client, alice)
    url = f"/api/routes/{created['id']}"
    assert (await client.put(url, json={"name": "x"})).status_code == 401
    r = await client.put(url, json={"name": "x"}, headers=bob.headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_route_owner"
    assert (await client.put(url, json={"name": "x"}, headers=manager.headers)).status_code == 403
    assert (await client.put(url, json={"name": "by admin"}, headers=admin.headers)).status_code == 200
    assert (await client.put("/api/routes/99999", json={"name": "x"}, headers=alice.headers)).status_code == 404


async def test_other_users_cannot_probe_private_routes_via_update_or_delete(client, alice, bob):
    created = await create_route(client, alice, is_public=False)
    url = f"/api/routes/{created['id']}"
    assert (await client.put(url, json={"name": "x"}, headers=bob.headers)).status_code == 404
    assert (await client.delete(url, headers=bob.headers)).status_code == 404


async def test_delete_permissions_and_effects(client, alice, bob, admin):
    created = await create_route(client, alice)
    url = f"/api/routes/{created['id']}"
    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(url, headers=bob.headers)).status_code == 403
    assert (await client.delete(url, headers=alice.headers)).status_code == 204
    assert (await client.get(url)).status_code == 404
    assert (await client.delete(url, headers=alice.headers)).status_code == 404
    other = await create_route(client, alice, "second")
    assert (await client.delete(f"/api/routes/{other['id']}", headers=admin.headers)).status_code == 204
