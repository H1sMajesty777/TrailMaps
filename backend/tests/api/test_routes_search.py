from tests.conftest import make_client
from tests.helpers import AMS_CENTER, UTR_A, UTR_B, UTR_C, create_route, make_actor

LONG = [UTR_A, UTR_B, UTR_C]  # ~2.5 km, ~30 min on foot


async def tag(client, staff, name):
    return (await client.post("/api/tags", json={"name": name}, headers=staff.headers)).json()


async def seed(client, alice, manager):
    food, walk = await tag(client, manager, "food"), await tag(client, manager, "walk")
    ams = await create_route(client, alice, "Amsterdam short", tag_ids=[food["id"]])
    utr = await create_route(
        client, alice, "Utrecht long", waypoints=LONG, tag_ids=[food["id"], walk["id"]]
    )
    await create_route(client, alice, "Private", is_public=False)
    return ams, utr


def names(response):
    return [i["name"] for i in response.json()["items"]]


async def test_default_listing_shows_only_public_visible_routes(client, alice, manager):
    await seed(client, alice, manager)
    r = await client.get("/api/routes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and sorted(names(r)) == ["Amsterdam short", "Utrecht long"]
    assert body["page"] == 1 and body["limit"] == 20 and body["pages"] == 1
    card = body["items"][0]
    assert card["geometry"]["type"] == "LineString" and "author" in card and "likes_count" in card
    assert "waypoints" not in card


async def test_radius_search_uses_postgis(client, alice, manager):
    await seed(client, alice, manager)
    near = await client.get("/api/routes", params={**AMS_CENTER, "radius": 5000})
    assert names(near) == ["Amsterdam short"]
    assert near.json()["items"][0]["distance_from_user"] <= 50
    wide = await client.get("/api/routes", params={**AMS_CENTER, "radius": 100000})
    assert sorted(names(wide)) == ["Amsterdam short", "Utrecht long"]
    default_radius = await client.get("/api/routes", params=AMS_CENTER)
    assert names(default_radius) == ["Amsterdam short"]


async def test_partial_overlap_with_search_area_counts(client, alice, manager):
    await seed(client, alice, manager)
    # centre ~1 km south of the route start: only the beginning of the line is inside 1.2 km
    r = await client.get("/api/routes", params={"lat": 52.0817, "lng": 5.1214, "radius": 1200})
    assert names(r) == ["Utrecht long"]


async def test_sort_by_distance(client, alice, manager):
    await seed(client, alice, manager)
    r = await client.get("/api/routes", params={**AMS_CENTER, "radius": 100000, "sort": "distance"})
    assert names(r) == ["Amsterdam short", "Utrecht long"]
    distances = [i["distance_from_user"] for i in r.json()["items"]]
    assert distances == sorted(distances) and distances[1] > 30000


async def test_filter_validation(client):
    for params in (
        {"lat": 52.0},
        {"lng": 4.0},
        {"radius": 100},
        {"sort": "distance"},
        {"duration_min": 100, "duration_max": 10},
        {"lat": 91, "lng": 0},
        {"radius": 0, "lat": 1, "lng": 1},
        {"sort": "bogus"},
        {"limit": 101},
        {"page": 0},
        {"tags_match": "some"},
    ):
        assert (await client.get("/api/routes", params=params)).status_code == 422, params


async def test_duration_filters(client, alice, manager):
    await seed(client, alice, manager)
    assert names(await client.get("/api/routes", params={"duration_min": 1000})) == ["Utrecht long"]
    assert names(await client.get("/api/routes", params={"duration_max": 1000})) == ["Amsterdam short"]
    both = await client.get("/api/routes", params={"duration_min": 100, "duration_max": 100000})
    assert both.json()["total"] == 2


async def test_tag_filters(client, alice, manager):
    await seed(client, alice, manager)
    assert sorted(names(await client.get("/api/routes", params={"tags": "food"}))) == [
        "Amsterdam short",
        "Utrecht long",
    ]
    assert names(await client.get("/api/routes", params={"tags": "WALK"})) == ["Utrecht long"]
    any_ = await client.get("/api/routes", params=[("tags", "walk"), ("tags", "nothing")])
    assert names(any_) == ["Utrecht long"]
    all_ = await client.get("/api/routes", params=[("tags", "walk"), ("tags", "food"), ("tags_match", "all")])
    assert names(all_) == ["Utrecht long"]
    none = await client.get("/api/routes", params=[("tags", "walk"), ("tags", "nothing"), ("tags_match", "all")])
    assert none.json()["total"] == 0
    assert (await client.get("/api/routes", params={"tags": "unknown"})).json()["items"] == []


async def test_combined_filters(client, alice, manager):
    await seed(client, alice, manager)
    r = await client.get(
        "/api/routes", params={**AMS_CENTER, "radius": 100000, "tags": "food", "duration_max": 1000}
    )
    assert names(r) == ["Amsterdam short"]


async def test_sorting_by_popularity_signals(client, alice, bob, manager):
    ams, utr = await seed(client, alice, manager)
    await client.post(f"/api/routes/{utr['id']}/like", headers=bob.headers)
    assert names(await client.get("/api/routes", params={"sort": "likes"}))[0] == "Utrecht long"
    assert names(await client.get("/api/routes", params={"sort": "popular"}))[0] == "Utrecht long"
    assert names(await client.get("/api/routes", params={"sort": "new"}))[0] == "Utrecht long"
    assert names(await client.get("/api/routes", params={"sort": "duration"}))[0] == "Amsterdam short"


async def test_pagination(client, alice):
    for i in range(5):
        await create_route(client, alice, f"route {i}")
    page1 = (await client.get("/api/routes", params={"limit": 2, "page": 1, "sort": "new"})).json()
    page3 = (await client.get("/api/routes", params={"limit": 2, "page": 3, "sort": "new"})).json()
    page4 = (await client.get("/api/routes", params={"limit": 2, "page": 4, "sort": "new"})).json()
    assert page1["total"] == 5 and page1["pages"] == 3 and len(page1["items"]) == 2
    assert [i["name"] for i in page1["items"]] == ["route 4", "route 3"]
    assert [i["name"] for i in page3["items"]] == ["route 0"]
    assert page4["items"] == [] and page4["total"] == 5


async def test_is_liked_flag_is_per_viewer(client, alice, bob, manager):
    ams, _ = await seed(client, alice, manager)
    await client.post(f"/api/routes/{ams['id']}/like", headers=bob.headers)
    as_bob = {i["name"]: i["is_liked"] for i in (await client.get("/api/routes", headers=bob.headers)).json()["items"]}
    as_anon = {i["name"]: i["is_liked"] for i in (await client.get("/api/routes")).json()["items"]}
    assert as_bob["Amsterdam short"] is True and as_bob["Utrecht long"] is False
    assert not any(as_anon.values())


async def test_cards_use_simplified_geometry_but_detail_is_full(client, alice):
    pts = [{"lat": 52.37 + i * 0.0005, "lng": 4.89 + (0.00001 if i % 2 else 0)} for i in range(10)]
    created = await create_route(client, alice, "straightish", waypoints=pts)
    card = (await client.get("/api/routes")).json()["items"][0]
    assert len(card["geometry"]["coordinates"]) < len(created["geometry"]["coordinates"])
    detail = (await client.get(f"/api/routes/{created['id']}")).json()
    assert len(detail["geometry"]["coordinates"]) == len(created["geometry"]["coordinates"])


async def test_list_cache_is_invalidated_on_every_mutation(app_factory):
    app = await app_factory(list_cache_ttl_seconds=60)
    async with make_client(app) as c:
        owner = await make_actor(app, c, "cacheowner")
        fan = await make_actor(app, c, "cachefan")
        admin = await make_actor(app, c, "cacheadmin", role="admin")
        first = await create_route(c, owner, "cached")
        assert (await c.get("/api/routes")).json()["items"][0]["likes_count"] == 0
        assert (await c.get("/api/routes")).json()["total"] == 1  # served from cache
        await c.post(f"/api/routes/{first['id']}/like", headers=fan.headers)
        assert (await c.get("/api/routes")).json()["items"][0]["likes_count"] == 1
        await c.put(f"/api/routes/{first['id']}", json={"name": "renamed"}, headers=owner.headers)
        assert (await c.get("/api/routes")).json()["items"][0]["name"] == "renamed"
        await c.put(f"/api/admin/routes/{first['id']}/visibility", json={"is_hidden": True}, headers=admin.headers)
        assert (await c.get("/api/routes")).json()["total"] == 0
        await c.put(f"/api/admin/routes/{first['id']}/visibility", json={"is_hidden": False}, headers=admin.headers)
        assert (await c.get("/api/routes")).json()["total"] == 1
        await c.delete(f"/api/routes/{first['id']}", headers=owner.headers)
        assert (await c.get("/api/routes")).json()["total"] == 0
        # personalised flag is not cached across users
        second = await create_route(c, owner, "again")
        await c.post(f"/api/routes/{second['id']}/like", headers=fan.headers)
        liked = (await c.get("/api/routes", headers=fan.headers)).json()["items"][0]["is_liked"]
        anon = (await c.get("/api/routes")).json()["items"][0]["is_liked"]
        assert liked is True and anon is False

