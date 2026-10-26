from tests.helpers import create_route


async def complain(client, actor, route_id, description="This route is inappropriate"):
    return await client.post(
        "/api/complaints",
        json={"route_id": route_id, "description": description},
        headers=actor.headers,
    )


async def test_user_can_file_a_complaint(client, alice, bob):
    route = await create_route(client, alice, "Reported one")
    r = await complain(client, bob, route["id"])
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending" and body["route_id"] == route["id"]
    assert body["route_name"] == "Reported one" and body["username"] == "bob"
    assert body["user_id"] == bob.id and body["description"] == "This route is inappropriate"


async def test_complaint_validation_and_auth(client, alice, bob):
    route = await create_route(client, alice)
    assert (await client.post("/api/complaints", json={"route_id": route["id"], "description": "abc"})).status_code == 401
    assert (await complain(client, bob, route["id"], "x")).status_code == 422
    assert (await complain(client, bob, route["id"], "y" * 2001)).status_code == 422
    r = await client.post("/api/complaints", json={"description": "no route"}, headers=bob.headers)
    assert r.status_code == 422
    assert (await complain(client, bob, 99999)).status_code == 404


async def test_cannot_report_own_or_invisible_routes(client, alice, bob):
    own = await create_route(client, alice)
    r = await complain(client, alice, own["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "own_route"
    private = await create_route(client, alice, is_public=False)
    assert (await complain(client, bob, private["id"])).status_code == 404


async def test_only_one_open_complaint_per_user_and_route(client, alice, bob, manager):
    route = await create_route(client, alice)
    first = (await complain(client, bob, route["id"])).json()
    dup = await complain(client, bob, route["id"])
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "complaint_already_pending"
    await client.put(f"/api/complaints/{first['id']}", json={"status": "resolved"}, headers=manager.headers)
    assert (await complain(client, bob, route["id"])).status_code == 201


async def test_listing_is_staff_only_with_filters(client, alice, bob, manager, admin):
    r1 = await create_route(client, alice, "one")
    r2 = await create_route(client, alice, "two")
    c1 = (await complain(client, bob, r1["id"])).json()
    await complain(client, bob, r2["id"])
    await client.put(f"/api/complaints/{c1['id']}", json={"status": "rejected"}, headers=manager.headers)

    assert (await client.get("/api/complaints")).status_code == 401
    assert (await client.get("/api/complaints", headers=alice.headers)).status_code == 403
    everything = (await client.get("/api/complaints", headers=manager.headers)).json()
    assert everything["total"] == 2
    pending = (await client.get("/api/complaints", params={"status": "pending"}, headers=admin.headers)).json()
    assert [c["route_name"] for c in pending["items"]] == ["two"]
    by_route = (await client.get("/api/complaints", params={"route_id": r1["id"]}, headers=manager.headers)).json()
    assert [c["id"] for c in by_route["items"]] == [c1["id"]]
    assert (await client.get("/api/complaints", params={"status": "bogus"}, headers=manager.headers)).status_code == 422
    page = (await client.get("/api/complaints", params={"limit": 1, "page": 2}, headers=manager.headers)).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["pages"] == 2


async def test_manager_changes_status(client, alice, bob, manager):
    route = await create_route(client, alice)
    c = (await complain(client, bob, route["id"])).json()
    url = f"/api/complaints/{c['id']}"
    r = await client.put(url, json={"status": "resolved"}, headers=manager.headers)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert (await client.put(url, json={"status": "bogus"}, headers=manager.headers)).status_code == 422
    assert (await client.put(url, json={}, headers=manager.headers)).status_code == 422
    assert (await client.put("/api/complaints/99999", json={"status": "resolved"}, headers=manager.headers)).status_code == 404
    assert (await client.put(url, json={"status": "pending"}, headers=bob.headers)).status_code == 403
    assert (await client.put(url, json={"status": "pending"})).status_code == 401


async def test_reopening_conflicts_with_a_newer_pending_complaint(client, alice, bob, manager):
    route = await create_route(client, alice)
    first = (await complain(client, bob, route["id"])).json()
    await client.put(f"/api/complaints/{first['id']}", json={"status": "resolved"}, headers=manager.headers)
    await complain(client, bob, route["id"])
    r = await client.put(f"/api/complaints/{first['id']}", json={"status": "pending"}, headers=manager.headers)
    assert r.status_code == 409


async def test_only_admin_can_hide_a_route_through_a_complaint(client, alice, bob, manager, admin):
    route = await create_route(client, alice)
    c = (await complain(client, bob, route["id"])).json()
    url = f"/api/complaints/{c['id']}"
    r = await client.put(url, json={"status": "resolved", "hide_route": True}, headers=manager.headers)
    assert r.status_code == 403
    assert (await client.get(f"/api/routes/{route['id']}")).status_code == 200  # nothing changed

    r = await client.put(url, json={"status": "resolved", "hide_route": True}, headers=admin.headers)
    assert r.status_code == 200
    assert (await client.get(f"/api/routes/{route['id']}")).status_code == 404
    assert (await client.get("/api/routes")).json()["total"] == 0
    assert (await client.get(f"/api/routes/{route['id']}", headers=alice.headers)).json()["is_hidden"] is True

    await client.put(url, json={"status": "rejected", "hide_route": False}, headers=admin.headers)
    assert (await client.get(f"/api/routes/{route['id']}")).status_code == 200


async def test_deleting_the_route_removes_its_complaints(client, alice, bob, manager):
    route = await create_route(client, alice)
    await complain(client, bob, route["id"])
    await client.delete(f"/api/routes/{route['id']}", headers=alice.headers)
    assert (await client.get("/api/complaints", headers=manager.headers)).json()["total"] == 0
