async def test_list_is_public_and_sorted(client, manager):
    for name in ("walk", "food"):
        assert (
            await client.post("/api/tags", json={"name": name}, headers=manager.headers)
        ).status_code == 201
    r = await client.get("/api/tags")
    assert r.status_code == 200
    assert [t["name"] for t in r.json()] == ["food", "walk"]


async def test_only_staff_can_create(client, alice, manager, admin):
    body = {"name": "views", "color": "#AABBCC"}
    assert (await client.post("/api/tags", json=body)).status_code == 401
    assert (await client.post("/api/tags", json=body, headers=alice.headers)).status_code == 403
    r = await client.post("/api/tags", json=body, headers=manager.headers)
    assert r.status_code == 201 and r.json()["color"] == "#aabbcc"
    assert (await client.post("/api/tags", json={"name": "other"}, headers=admin.headers)).status_code == 201


async def test_duplicate_names_are_case_insensitive(client, manager):
    await client.post("/api/tags", json={"name": "Food"}, headers=manager.headers)
    r = await client.post("/api/tags", json={"name": "food"}, headers=manager.headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "tag_exists"


async def test_validation(client, manager):
    for body in ({"name": ""}, {"name": "x", "color": "red"}, {"name": "y" * 51}):
        r = await client.post("/api/tags", json=body, headers=manager.headers)
        assert r.status_code == 422, body


async def test_update_and_delete_are_admin_only(client, alice, manager, admin):
    tag = (await client.post("/api/tags", json={"name": "old"}, headers=manager.headers)).json()
    url = f"/api/tags/{tag['id']}"
    assert (await client.put(url, json={"name": "new"}, headers=manager.headers)).status_code == 403
    r = await client.put(url, json={"name": "new", "color": "#112233"}, headers=admin.headers)
    assert r.status_code == 200 and r.json()["name"] == "new"
    assert (await client.delete(url, headers=manager.headers)).status_code == 403
    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (await client.delete(url, headers=admin.headers)).status_code == 404
    assert (await client.put(url, json={"name": "x"}, headers=admin.headers)).status_code == 404


async def test_rename_to_existing_name_conflicts(client, manager, admin):
    await client.post("/api/tags", json={"name": "a"}, headers=manager.headers)
    b = (await client.post("/api/tags", json={"name": "b"}, headers=manager.headers)).json()
    r = await client.put(f"/api/tags/{b['id']}", json={"name": "A"}, headers=admin.headers)
    assert r.status_code == 409
